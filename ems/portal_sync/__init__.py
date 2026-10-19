"""Sync a school's data from its own portal (spec 0005).

``fields`` lists the Ordo fields each endpoint kind maps, ``secrets`` keeps
the portal key encrypted, ``fetch`` calls the portal safely, ``config``
validates a connection's setup, ``runs`` starts a sync and applies what it
fetched: ``structure`` for departments (with their faculties), classes and
courses, then ``reconcile`` for students. ``refs`` resolves the departments
and classes a row names, and ``codes`` gives departments and faculties
their codes.
"""
