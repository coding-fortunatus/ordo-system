"""Resolving the departments and classes a row names (spec 0005, *Matching*).

A ``ref`` is a portal ID or a name: a portal ID match on ``external_id``
comes first, then the name (a class only within the row's department). A
name with no match is created (AC-7), with a derived code for a department
(AC-18), unless ``create`` is off (combined mode applies them in passes of
their own first). New records stay unsaved until ``save_new``, so a pass
that is held writes nothing. While a mass delete holds a type, a row naming one of
its missing records, or a record it would create, is refused (AC-11).
"""
from collections import defaultdict

from ems.models import Class, Department
from ems.portal_sync import codes


def norm(name):
    return (name or "").strip().lower()


def _limit(model, name):
    return model._meta.get_field(name).max_length


def class_portal_id(department_ref, name):
    """A class's portal ID when the portal sends none: class names repeat
    across departments, so its department ref is part of it."""
    return f"{department_ref}/{name}"


def _key(department):
    # New departments have no pk yet; their classes key on the object.
    return department.pk if department.pk else ("new", id(department))


class Refs:
    def __init__(self, results, *, create=True):
        self.results = results
        self.create = create
        self.new_departments = []
        self.new_classes = []
        self.dept_by_id = {}
        self.dept_by_name = defaultdict(list)
        self.taken = set()
        for dept in Department.objects.only("id", "name", "slug", "external_id"):
            self._index_department(dept)
            self.taken.add(dept.slug.upper())
        self.class_by_id = {}
        self.class_by_name = defaultdict(list)
        self.class_department = {}
        for cls in Class.objects.only("id", "name", "department_id", "external_id"):
            self._index_class(cls, cls.department_id)

    def _index_department(self, dept):
        if dept.external_id:
            self.dept_by_id[dept.external_id] = dept
        self.dept_by_name[norm(dept.name)].append(dept)

    def _index_class(self, cls, department_key):
        if cls.external_id:
            self.class_by_id[cls.external_id] = cls
        self.class_by_name[(department_key, norm(cls.name))].append(cls)
        self.class_department[id(cls)] = department_key

    def department(self, ref):
        """``(department, error)``; a name with no match is created."""
        dept = self.dept_by_id.get(ref)
        found = [dept] if dept else self.dept_by_name.get(norm(ref), [])
        if len(found) > 1:
            return None, f"More than one department is named '{ref}'."
        if found:
            if self.results.blocks("departments", found[0]):
                return None, f"Department '{ref}' is missing from the portal, and departments are held."
            return found[0], None
        if "departments" in self.results.held:
            return None, f"No department '{ref}' in Ordo, and departments are held."
        if not self.create:
            return None, f"No department '{ref}' in Ordo: see the departments issues."
        for field in ("name", "external_id"):
            if len(ref) > _limit(Department, field):
                return None, f"Department '{ref[:40]}…' is too long to create."
        dept = Department(
            name=ref,
            slug=codes.derive(ref, "DEPT", self.taken),
            external_id=ref,
        )
        self._index_department(dept)
        self.new_departments.append(dept)
        return dept, None

    def klass(self, ref, department, department_ref):
        """``(class, error)``, within ``department``; a name with no match
        is created there."""
        cls = self.class_by_id.get(ref)
        if cls is not None and self.class_department[id(cls)] != _key(department):
            return None, f"Class '{ref}' isn't in department '{department.name}'."
        found = [cls] if cls else self.class_by_name.get((_key(department), norm(ref)), [])
        if len(found) > 1:
            return None, f"More than one class '{ref}' in department '{department.name}'."
        if found:
            if self.results.blocks("classes", found[0]):
                return None, f"Class '{ref}' is missing from the portal, and classes are held."
            return found[0], None
        if "classes" in self.results.held:
            return None, f"No class '{ref}' in department '{department.name}', and classes are held."
        if not self.create:
            return None, f"No class '{ref}' in department '{department.name}': see the classes issues."
        portal_id = class_portal_id(department_ref, ref)
        if len(ref) > _limit(Class, "name") or len(portal_id) > _limit(Class, "external_id"):
            return None, f"Class name '{ref[:40]}' is too long to create."
        if portal_id in self.class_by_id:
            return None, f"Portal ID '{portal_id}' already belongs to another class."
        cls = Class(name=ref, department=department, external_id=portal_id)
        self._index_class(cls, _key(department))
        self.new_classes.append(cls)
        return cls, None

    def save_new(self):
        """Write the departments and classes rows named but Ordo lacked."""
        if self.new_departments:
            Department.objects.bulk_create(self.new_departments)
            self.results["departments"].add("created", [d.name for d in self.new_departments])
        if self.new_classes:
            # bulk_create picks up the pks the new departments just got.
            Class.objects.bulk_create(self.new_classes)
            self.results["classes"].add("created", [c.name for c in self.new_classes])
        self.new_departments, self.new_classes = [], []
