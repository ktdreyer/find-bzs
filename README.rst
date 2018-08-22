This very-hacky script finds Red Hat Bugzilla tickets that are fixed from one
Git tag to the next.

We use Red Hat Bugzilla's "External Trackers" feature to tie GitHub Pull
Requests to specific RHBZ bugs.

For a fast-moving package that involves weekly or daily rebases, it can be
tricky to understand exactly which BZs are fixed in each new upstream version.

When a developer wants to rebase a package from one Git tag to another, this
tool provides a report about which RHBZs would be fixed during the rebase.

Example Use
-----------

For example: let's say we're rebasing the ceph-ansible package from the
upstream "v3.0.3" Git tag to "v3.0.4".

Clone ceph-ansible from GitHub::

    $ cd ~/dev/
    $ git clone https://github.com/ceph/ceph-ansible
    $ cd ceph-ansible

(If you've already cloned ceph-ansible, just ``git pull`` to fetch all the
latest updates and tags.)

Now run the ``find-bzs.py`` script in your up-to-date clone directory::

    $ ../find-bzs/find-bzs.py
    ================

    * Mon Nov 13 2017 Ken Dreyer <kdreyer@redhat.com> - 3.0.11-1
    - Update to v3.0.11 (rhbz#1512538, rhbz#1511811, rhbz#1510906,
      rhbz#1509230)

    Command for RHEL dist-git:
    ================
    rdopkg new-version 3.0.11 -B "rhbz#1512538 rhbz#1511811 rhbz#1510906 rhbz#1509230"
    ================
    Command for Ubuntu dist-git:
    rhcephpkg new-version -B "rhbz#1512538 rhbz#1511811 rhbz#1510906 rhbz#1509230"
    ================
    Query for browsing:
    https://bugzilla.redhat.com/buglist.cgi?bug_id=1512538,1511811,1510906,1509230
    ================
    When RHEL and Ubuntu dist-git are committed:

    bugzilla modify -s MODIFIED -F "RHEL: ceph-ansible-3.0.11-1.el7cp Ubuntu: ceph-ansible_3.0.11-2redhat1" 1512538 1511811 1510906 1509230


The output gives you a ``%changelog`` entry to paste into the .spec file, and a
link for browsing the bugs to visually inspect them. The python-bugzilla
command will change the bug to MODIFIED and populate Fixed In Version.

GitHub Token
------------

Note: you must provide your `GitHub Personal Access Token
<https://github.com/settings/tokens>`_ as a single line in ``~/.githubtoken``
so find-bzs can authenticate to the search API.

::

    $ cat ~/.githubtoken
    aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d

The token does not need any special permissions. ``find-bzs.py`` uses this
token to query GitHub's search API at a faster rate than GitHub permits for
anonymous clients.

Git directory
-------------

``find-bzs.py`` must operate in a Git clone of ceph-ansible. It will first
check the ``origin`` remote to find the GitHub project (in order to query
Bugzilla). If ``origin`` does not look like GitHub, it will fall back to
checking the ``upstream`` remote.

This means that if you've added GitHub as an ``upstream`` remote to a dist-git
repository (like for rdopkg), you can use ``find-bzs.py`` in that dist-git
repository. Just remember to ``git fetch upstream`` to fetch all the latest Git
tags from GitHub.
