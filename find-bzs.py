#!/usr/bin/python
from __future__ import print_function
import errno
import json
import subprocess
import os
import re
import requests
from bugzilla import Bugzilla
from textwrap import TextWrapper, dedent
import time

"""
Find Bugzilla numbers that correlate to new upstream tags in GitHub.
"""

# These are ceph-ansible tags to compare:
# TODO: auto-determine "OLD" from ceph-3.0-rhel-7-candidate
# TODO: auto-determine "NEW" from git-decribe
OLD = 'v3.1.0rc10'
OLD = 'v3.1.0rc11'

GITHUBURL = 'https://github.com/'
GITHUBAPI = 'https://api.github.com/'
SEARCH = GITHUBAPI + 'search/issues?q=sha:{sha}+type:pr+is:merged+repo:{project}'  # NOQA: E501
TOKENFILE = os.path.expanduser('~/.githubtoken')
CACHEDIR = os.path.expanduser('~/.cache/find-bzs')
rate_limit = None

# Only query BZs in this product:
PRODUCT = 'Red Hat Ceph Storage'


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
    project = m.group(1)
    if project.endswith('.git'):
        return project[:-4]
    return project


def find_shas(old, new):
    """
    Find a set of Git shas (direct and cherry-picked).

    :param old:  Old Git ref, eg. "v3.0.0"
    :param new:  New Git ref, eg. "v3.0.1"
    """
    cmd = ['git', 'log', '%s..%s' % (old, new), '--no-decorate']
    # print(' '.join(cmd))
    output = subprocess.check_output(cmd)
    shas = set()
    for line in output.strip().split("\n"):
        # Direct sha1 for this commit:
        m = re.match("commit (\w+)$", line)
        if m:
            sha = m.group(1)
            shas.add(sha)
        # "cherry picked from sha1" in git commit log text:
        m = re.search("cherry picked from commit (\w+)", line)
        if m:
            sha = m.group(1)
            shas.add(sha)
    return shas


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


def find_cached_sha(sha):
    """ Return the parsed cached contents for this sha, or None. """
    cachefile = os.path.join(CACHEDIR, 'sha-%s' % sha)
    try:
        with open(cachefile, 'r') as f:
            return json.load(f)
    except (OSError, IOError) as e:
        if e.errno != errno.ENOENT:
            raise


def cache_sha(sha, data):
    try:
        os.makedirs(CACHEDIR)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    cachefile = os.path.join(CACHEDIR, 'sha-%s' % sha)
    with open(cachefile, 'w') as f:
        json.dump(data, f)


def github_token():
    """ Read token from ~/.githubtoken """
    token = None
    with open(TOKENFILE, 'r') as fh:
        for line in fh:
            if line.strip() == '' or line.lstrip().startswith('#'):
                continue
            if token is not None:
                raise ValueError('too many lines in %s' % TOKENFILE)
            token = line.strip()
    return token


def github_get(url):
    """ Get an API endpoint according to the search API rate limit """
    global rate_limit
    headers = {'Authorization': 'token %s' % github_token()}
    # Note search API requests are quite limited.
    # https://developer.github.com/v3/rate_limit/
    if rate_limit is None or rate_limit == 0:
        # print('Looking up GitHub API Rate remaining')
        rl_url = GITHUBAPI + 'rate_limit'
        r = requests.get(rl_url, headers=headers)
        data = r.json()
        rate_limit = data['resources']['search']['remaining']
    if rate_limit == 0:
        print('Exhausted GitHub search API rate.')
        now = time.time()
        reset = data['resources']['search']['reset']
        diff = reset - now
        diff += 1  # pad our sleep a bit
        print('Sleeping %d seconds until the search API rate resets.' % diff)
        time.sleep(diff)
    r = requests.get(url, headers=headers)
    # GitHub provides headers alongside all API responses:
    # 'X-RateLimit-Limit': '30',
    # 'X-RateLimit-Remaining': '0',
    # 'X-RateLimit-Reset': '1513879151',
    rate_limit = int(r.headers['X-RateLimit-Remaining'])
    return r


def find_pr_for_sha(sha, project):
    """ Return PR int where this sha was merged. """
    data = find_cached_sha(sha)
    if not data:
        # print('querying api.github.com for %s' % sha)
        url = SEARCH.format(sha=sha, project=project)
        r = github_get(url)
        if r.status_code != requests.codes.ok:
            print(r.json())
        r.raise_for_status()
        data = r.json()
        cache_sha(sha, data)
    if data['total_count'] < 1:
        # In this case, it was probably a bogus "cherry picked from" line.
        # This may be an accident when the developer cherry-picks from other
        # work-in-progress branches. Don't treat it as fatal for now.
        print('warning: could not find merged PR for %s' % sha)
        return None
    if data['total_count'] > 1:
        print(url)  # debugging
        raise RuntimeError('mutiple %s PRs for %s' % (project, sha))
    item = data['items'][0]
    return item['number']


def find_all_prs(old, new, project):
    """
    Return all the PR ID numbers that correspond to PRs between "old" and
    "new" Git refs for this GitHub project.
    """
    prs = set()
    for sha in find_shas(old, new):
        pr_id = find_pr_for_sha(sha, project)
        if pr_id:
            prs.add(pr_id)
    return prs


def find_all_bzs(bzapi, project, old, new):
    """
    Return all the BZ ID numbers that correspond to PRs between "old" and
    "new" Git refs for this GitHub project. """
    result = set()
    all_prs = find_all_prs(old, new, project)
    print('Searching Bugzilla for %d pull requests' % len(all_prs))
    for pr_id in all_prs:
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
        rhbzs = ' '.join(bz_strs)
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


def query_link(all_bzs):
    """ Return a query URL for all BZs, so the maintainer can visit them. """
    idstr = ','.join([str(bz) for bz in all_bzs])
    return 'https://bugzilla.redhat.com/buglist.cgi?bug_id=%s' % idstr


def rhcephpkg_command(all_bzs):
    rhbzs = ''
    if all_bzs:
        bz_strs = ['rhbz#%d' % bz for bz in all_bzs]
        rhbzs = ' '.join(bz_strs)
    result = 'rhcephpkg new-version'
    if rhbzs:
        result += ' -B "%s"' % rhbzs
    return result


def rdopkg_command(version, all_bzs):
    """
    Return a rdopkg cli string to paste & run

    For example:
    rdopkg new-version 3.0.8 -B "rhbz#1507907"
    """
    rhbzs = ''
    if version.startswith('v'):
        version = version[1:]
    if all_bzs:
        bz_strs = ['rhbz#%d' % bz for bz in all_bzs]
        rhbzs = ' '.join(bz_strs)
    result = 'rdopkg new-version %s' % version
    if rhbzs:
        result += ' -B "%s"' % rhbzs
    return result


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


print('================')
print(rpm_changelog(NEW, all_bzs))

if 'rc' not in NEW:
    print('Command for RHEL dist-git:')
    print('================')
    print(rdopkg_command(NEW, all_bzs))

print('================')
print('Command for Ubuntu dist-git:')
print(rhcephpkg_command(all_bzs))

print('================')
print('Query for browsing:')
print(query_link(all_bzs))

print('================')
print('When RHEL and Ubuntu dist-git are committed:')
print(bugzilla_command(NEW, all_bzs))
