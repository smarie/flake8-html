# -*- coding: utf-8 -*-
"""A plugin for flake8 to generate HTML reports.

This formatter plugin prints only summary information to stdout, while writing
a HTML report into the directory given by --htmldir.

"""
from __future__ import print_function
import sys
import re
import os
import os.path
import codecs
import datetime
from operator import attrgetter
from collections import namedtuple, Counter

from pygments import highlight
from collections import defaultdict
from pygments.lexers import PythonLexer
from pygments.formatters import HtmlFormatter
from flake8.formatting import base
from jinja2 import Environment, PackageLoader, Markup


jinja2_env = Environment(
    loader=PackageLoader('flake8_html')
)


#: A sequence of error code prefixes
#:
#: The first matching prefix determines the severity
SEVERITY_ORDER = [
    ('E9', 1),
    ('F', 1),
    ('E', 2),
    ('W', 2),
    ('C', 2),
    ('D', 3)
]
DEFAULT_SEVERITY = 3

SEVERITY_NAMES = [
    'high',
    'medium',
    'low'
]


def find_severity(code):
    """Given a flake8-style error code, return an ordinal severity."""
    for prefix, sev in SEVERITY_ORDER:
        if code.startswith(prefix):
            return sev
    return DEFAULT_SEVERITY


IndexEntry = namedtuple(
    'IndexEntry',
    'filename report_name error_count highest_sev'
)


class HTMLPlugin(base.BaseFormatter):
    """A plugin for flake8 to render errors as HTML reports."""

    def after_init(self):
        """Configure the plugin run."""
        self.report_template = jinja2_env.get_template('file-report.html')
        self.source_template = jinja2_env.get_template('annotated-source.html')
        if not self.options.htmldir:
            sys.exit('--htmldir must be given if HTML output is enabled')
        self.outdir = self.options.htmldir
        if not os.path.isdir(self.outdir):
            os.mkdir(self.outdir)
        self.files = []
        self.error_counts = {}
        self.file_count = 0

    def beginning(self, filename):
        """Reset the per-file list of errors."""
        self.file_count += 1
        self.errors = []
        self.by_code = defaultdict(list)

    def handle(self, error):
        """Record this error against the current file."""
        sev = find_severity(error.code)
        self.errors.append((error, sev))
        self.by_code[error.code].append(error)

    def finished(self, filename):
        """Write the HTML reports for filename."""
        report_filename = self.get_report_filename(filename, suffix='.report')
        source_filename = self.get_report_filename(filename, suffix='.source')

        if not self.errors:
            # If the files exist, they are out of date; remove them
            for f in [report_filename, source_filename]:
                if os.path.exists(f):
                    os.unlink(f)
            return

        with open(filename, 'rb') as f:
            source = f.read()

        orig_filename = filename
        filename = re.sub(r'^\./', '', filename)

        highest_sev = min(sev for e, sev in self.errors)
        self.files.append(IndexEntry(
            filename=filename,
            report_name=os.path.basename(report_filename),
            error_count=len(self.errors),
            highest_sev=highest_sev
        ))

        # Build an index of errors by code/description
        index = []
        counts = Counter()
        for code, errors in self.by_code.items():
            sev = find_severity(code)
            counts[sev] += 1
            e = min(errors, key=attrgetter('line_number'))
            unique_messages = len({e.text for e in errors})
            errs = sorted((e.line_number, e.text) for e in errors)
            index.append((
                sev,
                len(errors),
                code,
                e.text,
                e.line_number,
                unique_messages,
                errs
            ))
        index.sort(key=lambda r: (r[0], -r[1], r[2]))

        scores = []
        for sev, count in sorted(counts.items()):
            scores.append(
                '%s: %d' % (SEVERITY_NAMES[sev - 1], count)
            )
        print(orig_filename, "has issues:", *scores)

        # Build a mapping of errors by line
        by_line = defaultdict(list)
        for error, sev in self.errors:
            by_line[error.line_number].append((error, sev))
        # Build a table of severities by line
        line_sevs = {}
        for line, errs in by_line.items():
            line_sevs[line] = min(e[1] for e in errs)

        params = self._format_source(source)
        params.update(
            filename=filename,
            report_filename=os.path.basename(report_filename),
            source_filename=os.path.basename(source_filename),
            errors=by_line,
            line_sevs=line_sevs,
            highest_sev=highest_sev,
            index=index
        )
        rendered = self.report_template.render(**params)
        with codecs.open(report_filename, 'w', encoding='utf8') as f:
            f.write(rendered)
        rendered = self.source_template.render(**params)
        with codecs.open(source_filename, 'w', encoding='utf8') as f:
            f.write(rendered)

    def get_report_filename(self, filename, suffix=''):
        """Generate a path in the output directory for the source file given.

        If `suffix` is given, this is an additional string inserted into the
        path before the .html extension.

        """
        stem, ext = os.path.splitext(filename)
        rfname = '{}{}.html'.format(
            stem.replace(os.sep, '.').strip('.'),
            suffix
        )
        path = os.path.join(self.outdir, rfname)
        return path

    def _format_source(self, source):
        formatter = HtmlFormatter(nowrap=True)
        html = highlight(source, PythonLexer(), formatter)
        return {
            'html_lines': [Markup(l) for l in html.splitlines()],
            'css': formatter.get_style_defs()
        }

    def stop(self):
        """After the flake8 run, write the stylesheet and index."""
        self.write_styles()
        self.write_index()

    def write_styles(self):
        """Write the stylesheet."""
        formatter = HtmlFormatter(nowrap=True)
        tmpl = jinja2_env.get_template('styles.css')

        rendered = tmpl.render(
            pygments_css=formatter.get_style_defs()
        )

        stylesheet = os.path.join(self.outdir, 'styles.css')
        with codecs.open(stylesheet, 'w', encoding='utf8') as f:
            f.write(rendered)

    def write_index(self):
        """Write the index file."""
        if self.files:
            highest_sev = min(e.highest_sev for e in self.files)
        else:
            highest_sev = 4
        rendered = jinja2_env.get_template('index.html').render(
            file_count=self.file_count,
            index=sorted(
                self.files,
                key=lambda e: (e.highest_sev, -e.error_count)
            ),
            now=datetime.datetime.now(),
            versions=self.option_manager.generate_versions(),
            highest_sev=highest_sev
        )
        indexfile = os.path.join(self.outdir, 'index.html')
        with codecs.open(indexfile, 'w', encoding='utf8') as f:
            f.write(rendered)

    @classmethod
    def add_options(cls, options):
        """Add a --htmldir option to the OptionsManager."""
        cls.option_manager = options
        options.add_option(
            '--htmldir',
            help="Directory in which to write HTML output."
        )