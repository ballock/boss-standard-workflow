#!/usr/bin/python
""" Implements a simple changes file validator according to the
    common guidelines http://wiki.meego.com/Packaging/Guidelines#Changelogs

    Also checks that latest changelog version matches the version in spec file.

.. warning::
   Either the get_relevant_changelog or the get_changelog participant
   participants should have been run first to fetch the relevant changelog
   entries, or the full changelog


:term:`Workitem` fields IN:

:Parameters:
   ev.actions(list of dict):
      submit request data structure :term:`actions`
      each action annotated with key "relevant_changelog" (in relevant mode)
      relevant changelogs are formatted as lists of entries (which are strings)

   changelog(string):
      full package changelog (in full mode)
      usually placed there by get_changelog participant

:term:`Workitem` params IN

:Parameters:
   using(string):
      Optional parameter to specify mode "relevant_changelog" or "full"
      Default is full

:term:`Workitem` fields OUT:

:Returns:
   result(Boolean):
      True if the changes files of all packages are valid, False otherwise.

Check respects the values in [checks] section of packages boss.conf
for following keys:

    check_valid_changes:
        skip/warn this check

"""

import re
import time
import rpm
from tempfile import NamedTemporaryFile
from boss.rpm import parse_spec
from boss.checks import CheckActionProcessor
from buildservice import BuildService

MAX_ERRORS = 8

class Expected(Exception):
    _ref = "https://wiki.merproject.org/wiki/Packaging_guidelines#Changelogs"

    def __init__(self, found, expected, lineno, line=None):
        super(Expected, self).__init__()
        self.found = found
        self.expected = expected
        self.lineno = lineno
        if isinstance(line, unicode):
            self.line = line.encode("ascii", "replace")
        else:
            self.line = line

    def __str__(self):
        msg = ["\nFound unexpected %s at line %d, expected %s\n"
               % (self.found, self.lineno, ", ".join(self.expected))]
        if self.line:
            msg.append(self.line)
        msg.append("\nplease follow ref at %s" % self._ref)
        return "".join(msg)

class Invalid(Exception):
    _ref = "http://wiki.meego.com/Packaging/Guidelines#Changelogs"

    def __init__(self, invalid, missing=None, lineno=None, line=None):
        super(Invalid, self).__init__()
        self.invalid = invalid
        self.missing = missing
        self.lineno = lineno
        if isinstance(line, unicode):
            self.line = line.encode("ascii", "replace")
        else:
            self.line = line

    def __str__(self):
        if self.missing:
            msg = ["\nInvalid %s at line %d, maybe missing %s\n"
                   % (self.invalid, self.lineno, self.missing)]
        else:
            msg = ["\nInvalid %s at line %d\n" % (self.invalid, self.lineno)]
        if self.line:
            msg.append(self.line)
        msg.append("\nplease follow ref at %s" % self._ref)
        return "".join(msg)

class Validator(object):
    header_re = re.compile(r"^\* +(?P<date>\w+ +\w+ +\w+ +\w+) (?P<author>[^<]+?)?(?P<space> )?(?P<email><[^>]+>)?(?P<hyphen> *\-)? *(?P<version>[^ ]+)? *$")
    header_groups = ["date", "author", "email", "space", "hyphen", "version"]
    blank_re = re.compile(r"^$")
    body_re = re.compile(r"^-\s*\S.*$")
    continuation_re = re.compile(r"^\s+\S.*$")
    date_format = "%a %b %d %Y"

    after_header = ["body"]
    after_blank = ["EOF", "header","blank"]
    after_body = ["blank", "EOF", "body", "continuation line"]
    after_continuation = after_body

    def validate(self, changes):
        errors = []
        lineno = 0
        expect = ["header"]
        for line in changes.splitlines():
            lineno = lineno + 1
            line = line.rstrip("\n")
            if line.startswith("*"):
                # changelog header
                if "header" not in expect:
                    errors.append(Expected("header", expect, lineno=lineno))

                header = self.header_re.match(line)
                if not header:
                    errors.append(Invalid("header", lineno=lineno, line=line))
                    expect = self.after_header
                    continue

                for group in self.header_groups:
                    if not header.group(group):
                        errors.append(Invalid("header", missing=group,
                                      lineno=lineno, line=line))

                if header.group('date'):
                    try:
                        time.strptime(header.group('date'), self.date_format)
                    except ValueError:
                        errors.append(Invalid('date', lineno=lineno,
                                                  line=line))

                expect = self.after_header

            elif self.blank_re.match(line):
                if "blank" not in expect:
                    errors.append(Expected("blank", expect, lineno=lineno,
                        line=line))
                expect = self.after_blank

            elif self.body_re.match(line):
                if "body" not in expect:
                    errors.append(Expected("body", expect, lineno=lineno,
                        line=line))
                expect = self.after_body

            elif self.continuation_re.match(line):
                if "continuation line" not in expect:
                    errors.append(Expected("continuation line", expect,
                                   lineno=lineno, line=line))
                expect = self.after_continuation
            else:
                errors.append(Expected("garbage", expect, lineno=lineno,
                    line=line))
        return errors


class ParticipantHandler(object):
    """Participant class as defined by the SkyNET API"""

    def __init__(self):
        self.obs = None
        self.oscrc = None
        self.validator = Validator()

    def handle_wi_control(self, ctrl):
        """Job control thread"""
        pass

    def handle_lifecycle_control(self, ctrl):
        """Handle messages for the participant itself, like start and stop."""
        if ctrl.message == "start":
            if ctrl.config.has_option("obs", "oscrc"):
                self.oscrc = ctrl.config.get("obs", "oscrc")

    def setup_obs(self, namespace):
        """Setup the Buildservice instance

        :param namespace: Alias to the OBS apiurl.
        """
        self.obs = BuildService(oscrc=self.oscrc, apiurl=namespace)

    def check_spec_version_match(self, version, prj, pkg, rev=None):
        """Check that spec version matches given version"""
        spec = ""
        file_list = self.obs.getPackageFileList(prj, pkg, revision=rev)
        for fil in file_list:
            if fil.endswith(".spec"):
                spec = self.obs.getFile(prj, pkg, fil, revision=rev)
                break
        if not spec:
            return False, "No specfile in %s" % pkg
        with NamedTemporaryFile() as specf:
            specf.write(spec)
            specf.flush()
            try:
                specob = parse_spec(specf.name)
            except ValueError, exobj:
                return False, "Could not parse spec in %s: %s" % (pkg, exobj)
        src_hdrs = [pkg for pkg in specob.packages if pkg.header.isSource()][0]
        spec_version = src_hdrs.header[rpm.RPMTAG_VERSION]
        if spec_version != version.split('-')[0]:
            return False, "Last changelog version %s does not match" \
                          " version %s in spec file." % (version, spec_version)
        return True, None

    @CheckActionProcessor("check_valid_changes")
    def check_changelog(self, action, _wid):
        """Check changelog validity."""
        changes = action.get('relevant_changelog', None)
        if changes is None:
            return True, None

        changes = "\n".join(changes)
        errors = self.validator.validate(changes)
        ecount = len(errors)
        showing = MAX_ERRORS if ecount > MAX_ERRORS else ecount
        if errors:
            return False, "Changelog not valid, showing %d of %d errors:\n%s" \
                    % (showing, ecount, "\n".join(str(error) for
                        error in errors[:showing]))

        header = Validator.header_re.match(changes.splitlines()[0])
        if header:
            version = header.group("version")
            return self.check_spec_version_match(version,
                    action["sourceproject"], action["sourcepackage"],
                    action.get("sourcerevision", None))

        return True, None

    def handle_wi(self, wid):
        """Handle a workitem: do the quality check."""

        wid.result = False
        if not wid.fields.msg:
            wid.fields.msg = []
        using = wid.params.using or "full"

        if not wid.fields.ev or wid.fields.ev.namespace is None:
            raise RuntimeError("Missing mandatory field 'ev.namespace'")

        self.setup_obs(wid.fields.ev.namespace)

        if using == "relevant_changelog":
            if not wid.fields.ev or wid.fields.ev.actions is None:
                raise RuntimeError("Missing mandatory field 'ev.actions'")
            result = True
            for action in wid.fields.ev.actions:
                pkg_result, _ = self.check_changelog(action, wid)
                result = result and pkg_result
        elif using == "full":
            if not wid.fields.changelog:
                raise RuntimeError("Missing mandatory field 'changelog'")
            action = {"type": "submit",
                    "sourceproject": wid.fields.project,
                    "sourcepackage": wid.fields.package,
                    "relevant_changelog": [wid.fields.changelog]}
            result, _ = self.check_changelog(action, wid)
        else:
            raise RuntimeError("Unknown mode '%s' for parameter 'using'" %
                    using)

        wid.result = result
