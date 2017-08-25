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

# These are ceph-ansible tags to compare:
OLD = 'v3.0.0rc2'
NEW = 'v3.0.0rc3'


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
    """ Return an external tracker ID suitable for BZ searching. """
    # project is eg. "ceph/ceph-ansible"
    # ext_id is "ceph/ceph-ansible/pull/1234"
    return project + '/pull/' + str(pr_id)


def find_by_external_tracker(bzapi, project, pr_id):
    """ Return a list of bug ID numbers for this project / PR id number. """
    ext_id = external_tracker(project, pr_id)
    payload = {
        'include_fields': ['id', 'summary', 'status'],
        'f1': 'external_bugzilla.url',
        'o1': 'substring',
        'v1': GITHUBURL,
        'f2': 'ext_bz_bug_map.ext_bz_bug_id',
        'o2': 'equals',
        'v2': ext_id,
    }
    result = bzapi._proxy.Bug.search(payload)
    return [int(bz['id']) for bz in result['bugs']]


def rpm_version(version):
    """ Return an RPM version for %changelog """
    # eg "3.0.0-1"
    # or "3.0.0-0.1.rc1"
    if version.startswith('v'):
        version = version[1:]
    if 'rc' not in version:
        return version + '-1'
    (first, rc) = version.split('rc')
    return '%s-0.1.rc%s' % (first, rc)


def find_all_bzs(bzapi, project, old, new):
    """
    Return all the BZ ID numbers that correspond to PRs between "old" and
    "new" Git refs for this GitHub project. """
    result = []
    for l in git_merge_log(old, new):
        m = re.search("Merge pull request #(\d+)", l)
        if not m:
            raise RuntimeError('could not parse PR from %s' % l)
        pr_id = int(m.group(1))
        # print('searching for ceph-ansible PR %d' % pr_id)
        pr_bzs = find_by_external_tracker(bzapi, project, pr_id)
        result.extend(pr_bzs)
    return result


def rpm_changelog(version, all_bzs):
    """ Return an RPM %changelog string """
    # TODO: Debian as well here?
    changes = 'Update to %s' % version
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


bzapi = get_bzapi()
project = github_project()
all_bzs = find_all_bzs(bzapi, project, OLD, NEW)

print(rpm_changelog(NEW, all_bzs))
