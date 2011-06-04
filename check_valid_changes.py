#!/usr/bin/python
""" Quality check participant """

import re
from buildservice import BuildService

class Expected(Exception):

    _ref = "http://wiki.meego.com/Packaging/Guidelines#Changelogs"

    def __init__(self, found, expected, lineno, line=None):
        super(Expected, self).__init__()
        self.found = found
        self.expected = expected
        self.lineno = lineno
        self.line = line

    def __str__(self):
        msg =  ["\nFound unexpected %s at line %d, expected %s\n" % (self.found,
                                                                 self.lineno,
                                                      ", ".join(self.expected))]
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
        self.line = line

    def __str__(self):
        if self.missing:
            msg = ["\nInvalid %s at line %d, maybe missing %s\n" % (
                                                                 self.invalid,
                                                                 self.lineno,
                                                                 self.missing)]
        else:
            msg = ["\nInvalid %s at line %d\n" % (self.invalid, self.lineno)]
        if self.line:
            msg.append(self.line)
        msg.append("\nplease follow ref at %s" % self._ref)

        return "".join(msg)

class Validator(object):
    header_re = re.compile(r"^\* +(?P<date>\w+ +\w+ +\w+ +\w+) (?P<author>[^<]+)? ?(?P<email><.*>)?(?P<hyphen> *\-)? *(?P<version>[^ ]+)? *$")
    header_groups = ["date", "author", "email", "hyphen", "version"]
    blank_re = re.compile(r"^$")
    body_re = re.compile(r"^[-\s]\s*\S.*$")

    after_header = ["body"]
    after_blank = ["EOF", "header","blank"]
    after_body = ["blank", "EOF"]
    initial_expect = ["header"]

    def validate(self, changes):
        lineno = 0
        changes = changes.split("\n")
        for line in changes:
            lineno = lineno + 1
            line = line.rstrip("\n")
            if line.startswith("*"):
                # changelog header
                if "header" not in self.initial_expect:
                    raise Expected("header", self.initial_expect, lineno=lineno)

                header = self.header_re.match(line)
                if not header:
                    raise Invalid("header", lineno=lineno, line=line)
            
                for group in self.header_groups:
                    if not header.group(group):
                        raise Invalid("header", missing=group, lineno=lineno, line=line)

                    expect = self.after_header
                    continue

            if self.blank_re.match(line):
                if "blank" not in expect:
                    raise Expected("blank", expect, lineno=lineno, line=line)
                expect = self.after_blank
                continue

            if self.body_re.match(line):
                if "body" not in expect:
                    raise Expected("body", expect, lineno=lineno, line=line)
                expect = self.after_body
                continue

class ParticipantHandler(object):

    """ Participant class as defined by the SkyNET API """

    def __init__(self):
        self.obs = None
        self.oscrc = None
        self.validator = None

    def handle_wi_control(self, ctrl):
        """ job control thread """
        pass
    
    def handle_lifecycle_control(self, ctrl):
        """ participant control thread """
        if ctrl.message == "start":
            if ctrl.config.has_option("obs", "oscrc"):
                self.oscrc = ctrl.config.get("obs", "oscrc")
            self.validator = Validator()

    def setup_obs(self, namespace):
        """ setup the Buildservice instance using the namespace as an alias
            to the apiurl """

        self.obs = BuildService(oscrc=self.oscrc, apiurl=namespace)
    
    def get_changes_file(self, prj, pkg, rev=None):

        """ Get a package's changes file """

        changes = ""
        file_list = self.obs.getPackageFileList(prj, pkg, revision=rev)
        for fil in file_list:
            if fil.endswith(".changes"):
                changes = self.obs.getFile(prj, pkg, fil, revision=rev)
        return changes

    def quality_check(self, wid):

        """ Quality check implementation """

        wid.result = False
        if not wid.fields.msg:
            wid.fields.msg = []
        actions = wid.fields.ev.actions
        using = wid.params.using

        if not actions:
            wid.fields.__error__ = "Mandatory field: actions does not exist."
            wid.fields.msg.append(wid.fields.__error__)
            raise RuntimeError("Missing mandatory field")

        result = True
        for action in actions:
            changes = None
            if using == "relevant_changelog":
                changes = action['relevant_changelog']
            else:
                changes = self.get_changes_file(action["sourceproject"],
                                                action["sourcepackage"],
                                                action["sourcerevision"])

            # Assert validity of changes
            try:
                self.validator.validate(changes)
            except Invalid, msg:
                wid.fields.msg.extend(msg)
                result = False
            except Expected, msg:
                wid.fields.msg.extend(msg)
                result = False

        if not result:
            wid.fields.__error__ = "Some changelogs were invalid"

        wid.result = result

    def handle_wi(self, wid):

        """ actual job thread """

        # We may want to examine the fields structure
        if wid.fields.debug_dump or wid.params.debug_dump:
            print wid.dump()

        self.setup_obs(wid.namespace)
        self.quality_check(wid)
