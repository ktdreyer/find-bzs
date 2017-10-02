#!/usr/bin/python
from __future__ import print_function
import subprocess
import re
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
OLD = 'v3.0.0rc13'
NEW = 'v3.0.0rc14'


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


def git_merge_log(old, new):
    """ Return a list of Git merge commit logs. """
    cmd = ['git', 'log', '--oneline', '%s..%s' % (old, new), '--merges',
           '--no-decorate']
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


def find_all_bzs(bzapi, project, old, new):
    """
    Return all the BZ ID numbers that correspond to PRs between "old" and
    "new" Git refs for this GitHub project. """
    result = set()
    for l in git_merge_log(old, new):
        m = re.search("Merge pull request #(\d+)", l)
        if not m:
            raise RuntimeError('could not parse PR from %s' % l)
        pr_id = int(m.group(1))
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


bzapi = get_bzapi()
project = github_project()
all_bzs = find_all_bzs(bzapi, project, OLD, NEW)

print(rpm_changelog(NEW, all_bzs))

print('================')
print(links(all_bzs))
