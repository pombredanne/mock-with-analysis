from collections import namedtuple
import glob
import os

from firehose.report import Analysis, Issue, Visitor

def get_filename(file_):
    return file_.abspath.strip('/build/builddir/BUILD')

def get_internal_filename(file_):
    '''
    Given a File with an absolute path within an unpacked tarball within a
    buildroot:
    e.g.:
       '/builddir/build/BUILD/python-ethtool-0.7/python-ethtool/etherinfo.c'

    split out the trailing path fragment relative to the top of the tarball
    e.g.:
       'python-ethtool/etherinfo.c'

    The aim is to support comparisons between builds of different versions
    of the code.
    '''
    path = file_.abspath.strip('/build/builddir/BUILD')
    components = path.split('/')
    return os.path.join(*components[1:])

class ResultsDir:
    """
    Models a 'static-analysis' subdir as emitted by mock-with-analysis
    """
    def __init__(self, path):
        self.path = path

    def get_reports_dir(self):
        return os.path.join(self.path, 'reports')

    def get_sources_dir(self):
        return os.path.join(self.path, 'sources')

    def get_analyses(self):
        analyses = []
        for filename in glob.glob(os.path.join(self.get_reports_dir(), '*.xml')):
            r = Analysis.from_xml(filename)
            analyses.append( (filename, r) )
        return analyses

class AnalysisIssue(namedtuple('AnalysisIssue',
                               ['analysis', 'issue'])):
    def cmp(self, other):
        c = cmp(self.abspath,
                other.abspath)
        if c:
            return c
        c = cmp(self.line,
                other.line)
        if c:
            return c
        return 0

    @property
    def message(self):
        return self.issue.message

    @property
    def notes(self):
        return self.issue.notes

    @property
    def generator(self):
        return self.analysis.metadata.generator

    @property
    def testid(self):
        return self.issue.testid

    @property
    def givenpath(self):
        return self.issue.location.file.givenpath

    @property
    def abspath(self):
        return self.issue.location.file.abspath

    @property
    def internal_filename(self):
        return get_internal_filename(self.file_)

    @property
    def function(self):
        return self.issue.location.function

    @property
    def line(self):
        return self.issue.location.line

    @property
    def column(self):
        return self.issue.location.column

    @property
    def file_(self):
        return self.issue.location.file

    @property
    def trace(self):
        return self.issue.trace

class Model:
    def __init__(self, rdir):
        self.rdir = rdir
        self._analyses = [r for (filename, r) in self.rdir.get_analyses()]

    def iter_analyses(self):
        return self._analyses

    def open_file(self, file_):
        path = os.path.join(self.rdir.get_sources_dir(), file_.hash_.hexdigest)
        return open(path)

    def get_file_content(self, file_):
        with self.open_file(file_) as sourcefile:
            return sourcefile.read()

    def iter_analysis_issues(self):
        for analysis in self._analyses:
            for result in analysis.results:
                if isinstance(result, Issue):
                    yield AnalysisIssue(analysis, result)

    def get_source_files(self):
        """
        Get a sorted list of all File instances of interest
        """
        result = set()
        for a in self.iter_analyses():
            class FindFiles(Visitor):
                def visit_file(self, file_):
                    result.add(file_)
            a.accept(FindFiles())
        return sorted(list(result),
                      lambda f1, f2: cmp(f1.abspath, f2.abspath))

    def get_generators(self):
        """
        Get all Generator instances that were run
        """
        result = set()
        for a in self.iter_analyses():
            class FindGenerators(Visitor):
                def visit_generator(self, generator):
                    result.add(generator)
            a.accept(FindGenerators())
        return sorted(list(result),
                      lambda f1, f2: cmp(f1.name, f2.name))

    def get_analysis_issues_by_source(self):
        result = {}
        for ai in self.iter_analysis_issues():
            key = ai.file_
            if key in result:
                result[key].add(ai)
            else:
                result[key] = set([ai])
        return result

    def get_analysis_issues_by_source_and_generator(self):
        result = {}
        for ai in self.iter_analysis_issues():
            key = (ai.file_, ai.generator)
            if key in result:
                result[key].add(ai)
            else:
                result[key] = set([ai])
        return result

class SourceHighlighter:
    def __init__(self):
        from pygments.styles import get_style_by_name
        from pygments.formatters import HtmlFormatter

        # Get ready to use Pygments:
        self.style = get_style_by_name('default')
        self.formatter = HtmlFormatter(classprefix='source_',
                                       linenos='inline')

    def highlight(self, code):
        from pygments import highlight
        from pygments.lexers import CLexer
        html = highlight(code,
                         CLexer(),
                         # FIXME: ^^^ this hardcodes the source language
                         # (e.g. what about C++?)
                         self.formatter)
        return html

    def highlight_file(self, file_, model):
        if file_ is None:
            return ''
        result = ''
        with model.open_file(file_) as sourcefile:
                code = sourcefile.read()
        for i, line in enumerate(self.highlight(code).splitlines()):
            result += '<a id="file-%s-line-%i"/>' % (file_.hash_.hexdigest, i + 1)
            result += line
            result += '\n'
        return result

def make_issue_note(ai):
    html = '<div class="inline-error-report">'
    html += '   <div class="inline-error-report-message">%s</div>' % ai.message.text
    if ai.notes:
        html += '   <div class="inline-error-report-notes">%s</div>' % ai.notes.text
    html += '   <div class="inline-error-report-generator">(emitted by %s)</div>' % ai.generator.name
    if ai.trace:
        html += '<p>TODO: a detailed trace is available in the data model (not yet rendered in this report)</p>'
    html += '</div>'
    return html

def write_issue_table_for_file(f, file_, ais):
    f.write('    <table>\n')
    f.write('    <tr>\n')
    f.write('      <th>Location</th>\n')
    f.write('      <th>Tool</th>\n')
    f.write('      <th>Test ID</th>\n')
    f.write('      <th>Function</th>\n')
    f.write('      <th>Issue</th>\n')
    f.write('    </tr>\n')
    for ai in sorted(ais, AnalysisIssue.cmp):
        f.write('    <tr>\n')
        f.write('      <td>%s:%i:%i</td>\n'
                % (ai.givenpath,
                   ai.line,
                   ai.column))
        f.write('      <td>%s</td>\n' % ai.generator.name)
        f.write('      <td>%s</td>\n' % (ai.testid if ai.testid else ''))
        f.write('      <td>%s</td>\n' % (ai.function.name if ai.function else '')),
        f.write('      <td><a href="%s">%s</a></td>\n'
                % ('#file-%s-line-%i' % (file_.hash_.hexdigest, ai.line),
                   ai.message.text))
        f.write('    </tr>\n')
    f.write('    </table>\n')