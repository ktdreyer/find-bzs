This very-hacky script finds Red Hat Bugzilla tickets that are fixed from one
Git tag to the next.

We use Red Hat Bugzilla's "External Trackers" feature to tie GitHub Pull
Requests to specific RHBZ bugs.

For a fast-moving package that involves weekly or daily rebases, it can be
tricky to understand exactly which BZs are fixed in each new upstream version.

When a developer wants to rebase a package from one Git tag to another, this
tool provides a report about which RHBZs would be fixed during the rebase.

For example: let's say we're rebasing the ceph-ansible package from the
upstream "v3.0.3" Git tag to "v3.0.4".

Update your local ceph-ansible clone::

    $ cd ~/dev/ceph-ansible
    $ git checkout stable-3.0
    $ git pull

And run the ``find-bzs.py`` script in your up-to-date clone directory::

    $ ../find-bzs/find-bzs.py
    warning: could not find PR for 302e563601cd6820b1ae44fabdfb1506688c7c9b

    * Fri Oct 20 2017 Ken Dreyer <kdreyer@redhat.com> - 3.0.4-1
    - Update to v3.0.4 (rhbz#1500470)

    ================
    Links for browsing:
    https://bugzilla.redhat.com/1500470
    ================
    When RHEL and Ubuntu dist-git are committed:

    bugzilla modify -s MODIFIED -F "RHEL: ceph-ansible-3.0.4-1.el7cp Ubuntu: ceph-ansible_3.0.4-2redhat1" 1500470

The output gives you a ``%changelog`` entry to paste into the .spec file, and a
link for browsing the bugs to visually inspect them. The python-bugzilla
command will change the bug to MODIFIED and populate Fixed In Version.
