#!/usr/bin/python
from __future__ import print_function
import subprocess
import os
import re
import sys
from bugzilla import Bugzilla
from textwrap import TextWrapper, dedent
import time

"""
Find Bugzilla numbers that correlate to new upstream tags in GitHub.
"""

GITHUBURL = 'https://github.com/'

# Only query BZs in this product:
PRODUCT = 'Red Hat Ceph Storage'

# These are ceph-ansible tags to compare:
# TODO: auto-determine "OLD" from ceph-3.0-rhel-7-candidate
# TODO: auto-determine "NEW" from git-decribe
OLD = 'v3.0.7'
NEW = 'v3.0.8'


def github_project():
    """
    returns the GitHub project name for a Git clone in cwd.

    For example: "ceph/ceph-ansible"
    raises RuntimeError if the "origin" remote does not look like GitHub.
    """
    cmd = ['git', 'remote', 'get-url', 'origin']
    url = subprocess.check_output(cmd).strip()
    m = re.match('git@github.com:(.+)', url)
    if not m:
        raise RuntimeError('could not parse remote url %s' % url)
    return m.group(1)


def git_log(old, new, merges):
    """ Return a list of Git commit logs. """
    cmd = ['git', 'log', '%s..%s' % (old, new), '--no-decorate']
    if merges:
        cmd.extend(['--merges', '--oneline'])
    else:
        cmd.append('--no-merges')
    # print(' '.join(cmd))
    log = subprocess.check_output(cmd).strip().split("\n")
    return log


def get_bzapi():
    """ Return a logged-in RHBZ API instance """
    bzapi = Bugzilla('bugzilla.redhat.com')
    if not bzapi.logged_in:
        raise SystemExit('Not logged into BZ')
    return bzapi


def external_tracker(project, pr_id):
    """
    Return an external tracker ID suitable for BZ searching.

    :param project: GitHub project, eg "ceph/ceph-ansible"
    :param pr_id: ``int``, numerical PR ID, eg. "1234"
    :returns: Full external tracker ID, eg "ceph/ceph-ansible/pull/1234".
              Search Bugzilla's "External Trackers" for this value.
    """
    return project + '/pull/' + str(pr_id)


def find_by_external_tracker(bzapi, project, pr_id):
    """ Return a set of bug ID numbers for this project / PR id number. """
    ext_id = external_tracker(project, pr_id)
    payload = {
        'product': PRODUCT,
        'include_fields': ['id', 'summary', 'status'],
        'f1': 'external_bugzilla.url',
        'o1': 'substring',
        'v1': GITHUBURL,
        'f2': 'ext_bz_bug_map.ext_bz_bug_id',
        'o2': 'equals',
        'v2': ext_id,
        'f3': 'bug_status',
        'o3': 'notequals',
        'v3': 'CLOSED',
    }
    result = bzapi._proxy.Bug.search(payload)
    return set([int(bz['id']) for bz in result['bugs']])


def rpm_version(ref):
    """ Return an RPM version for this ref for %changelog """
    # eg "3.0.0-1"
    # or "3.0.0-0.1.rc1"
    cmd = ['git', 'describe', '--tags', ref]
    gitdescribe = subprocess.check_output(cmd).strip()
    try:
        (version, commits, sha) = gitdescribe.split('-')
    except ValueError:
        version = gitdescribe
        commits = None
        sha = None
    if version.startswith('v'):
        version = version[1:]
    if commits:
        # This was not a tagged ref. Just do something.
        release = '%s.%s' % (commits, sha)
        if 'rc' in version:
            (_, rc) = version.split('rc')
            release = '0.1.rc%s.%s' % (rc, release)
            version = version.replace('rc%s' % rc, '')
    else:
        # This was a tagged ref. Follow the Fedora pkging guidelines
        release = 1
        if 'rc' in version:
            (_, rc) = version.split('rc')
            release = '0.1.rc%s' % rc
            version = version.replace('rc%s' % rc, '')
    return '%s-%s' % (version, release)


def deb_version(ref):
    """ Return an DEB version for this ref for debian/changelog """
    # eg "3.0.0-2redhat1"
    # or "3.0.0~rc1-2redhat1"
    cmd = ['git', 'describe', '--tags', ref]
    gitdescribe = subprocess.check_output(cmd).strip()
    try:
        (version, commits, sha) = gitdescribe.split('-')
    except ValueError:
        version = gitdescribe
        commits = None
        sha = None
    if version.startswith('v'):
        version = version[1:]
    if commits:
        # This was not a tagged ref. Just do something.
        # I am not sure what the Debian convention is here...
        raise NotImplementedError()
        # TODO: see rpm_version() for inspiration
    else:
        # This was a tagged ref. Follow the Debian pkging guidelines
        release = '2redhat1'
        if 'rc' in version:
            version = version.replace('rc', '~rc')
    return '%s-%s' % (version, release)


def find_pr_for_sha(sha):
    """ Return PR int where this sha was merged. """

    # Requires a special .git/config option, fetching all PR refs into origin.
    # https://stackoverflow.com/questions/17818167/find-a-pull-request-on-github-where-a-commit-was-originally-created
    # git config --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pull/*  # NOQA: E501

    # Only run this if we don't have a local copy of this sha:
    cmd = ['git', 'cat-file', '-e', '%s^{commit}' % sha]
    with open(os.devnull, 'w') as FNULL:
        retcode = subprocess.call(cmd, stdout=FNULL, stderr=subprocess.STDOUT)
    if retcode != 0:
        output = subprocess.check_call(['git', 'fetch', 'origin'])

    cmd = ['git', 'describe', '--all', '--contains', sha]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    if sys.version_info >= (3, 0):
        output = output.decode('utf-8')
    output = output.splitlines()
    if len(output) > 1:
        raise RuntimeError('too many lines in %s' % output)
    if output[0].startswith('Could not get'):
        # In this case, it was probably a bogus "cherry picked from" line.
        # This may be an accident when the developer cherry-picks from other
        # work-in-progress branches. Don't treat it as fatal for now.
        print('warning: could not find PR for %s' % sha)
        return None
    m = re.match('remotes/origin/pull/(\d+)', output[0])
    if not m:
        raise RuntimeError('could not find PR ID number in %s' % output[0])
    id_ = int(m.group(1))
    return id_


def find_all_prs(old, new):
    """
    Return all the PR ID numbers that correspond to PRs between "old" and
    "new" Git refs for this GitHub project.
    """
    result = set()
    # XXX: I wonder if we could reduce the two loops into one here, and just
    # use the single find_pr_for_sha() method for everything.
    for line in git_log(old, new, merges=True):
        m = re.search("Merge pull request #(\d+)", line)
        if not m:
            raise RuntimeError('could not parse PR from %s' % line)
        pr_id = int(m.group(1))
        result.add(pr_id)
    for line in git_log(old, new, merges=False):
        # Discover a the original PR as well (if it exists):
        # 1) read the logs of all commits in this range,
        # 2) Parse the "cherry picked from" lines and collect all the shas,
        # 3) Find the PR numbers (ie, on master) that correlated to those shas.
        m = re.search("cherry picked from commit (\w+)", line)
        if m:
            sha = m.group(1)
            pr_id = find_pr_for_sha(sha)
            if pr_id:
                result.add(pr_id)
    return result


def find_all_bzs(bzapi, project, old, new):
    """
    Return all the BZ ID numbers that correspond to PRs between "old" and
    "new" Git refs for this GitHub project. """
    result = set()
    for pr_id in find_all_prs(old, new):
        # print('searching for ceph-ansible PR %d' % pr_id)
        pr_bzs = find_by_external_tracker(bzapi, project, pr_id)
        result = result | pr_bzs
    return result


def rpm_changelog(version, all_bzs):
    """ Return an RPM %changelog string """
    # TODO: Debian as well here?
    changes = 'Update to %s' % version
    if not version.startswith('v'):
        cmd = ['git', 'rev-parse', version]
        ref = subprocess.check_output(cmd).strip()
        changes = '%s (%s)' % (changes, ref)
    if all_bzs:
        bz_strs = ['rhbz#%d' % bz for bz in all_bzs]
        rhbzs = ', '.join(bz_strs)
        changes = '%s (%s)' % (changes, rhbzs)
    wrapper = TextWrapper(initial_indent='- ', subsequent_indent='  ')
    changes = wrapper.fill(changes)
    date = time.strftime('%a %b %d %Y')
    changelog = dedent("""
    * {date} {author} <{email}> - {version}
    {changes}
    """)
    return changelog.format(
        date=date,
        author='Ken Dreyer',
        email='kdreyer@redhat.com',
        version=rpm_version(version),
        changes=changes,
    )


def links(all_bzs):
    """ Return a string of all BZ URLs, so maintainer can visit them. """
    urls = ['https://bugzilla.redhat.com/%i' % bz for bz in all_bzs]
    return "\n".join(urls)


def bugzilla_command(version, all_bzs):
    """ Return a bugzilla cli string to paste & run """
    package = 'ceph-ansible'
    rpm_ver = rpm_version(version)
    deb_ver = deb_version(version)
    bzs = ' '.join(str(bz) for bz in all_bzs)
    command = dedent("""
    bugzilla modify -s MODIFIED -F "RHEL: {package}-{rpm_ver}.el7cp Ubuntu: {package}_{deb_ver}" {bzs}
    """)
    return command.format(
        package=package,
        rpm_ver=rpm_ver,
        deb_ver=deb_ver,
        bzs=bzs)


bzapi = get_bzapi()
project = github_project()
all_bzs = find_all_bzs(bzapi, project, OLD, NEW)

print(rpm_changelog(NEW, all_bzs))

print('================')
print('Links for browsing:')
print(links(all_bzs))

print('================')
print('When RHEL and Ubuntu dist-git are committed:')
print(bugzilla_command(NEW, all_bzs))
