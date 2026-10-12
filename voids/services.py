"""Rules for voids: area and supervisor from the postcode, and the overall status."""

from audit.models import record

from django.utils import timezone

from .models import PostcodeDistrict, Supervisor, TradeTask, Void, VoidUpdate, district_of

S = TradeTask.Status


def apply_postcode(void, overwrite=False):
    """Fill the area and patch supervisor from the postcode district."""
    d = PostcodeDistrict.objects.select_related("area", "supervisor").filter(
        district=district_of(void.postcode)).first()
    if not d:
        return
    if d.area and (overwrite or not void.area_id):
        void.area = d.area
    if d.supervisor and (overwrite or not void.supervisor_id):
        void.supervisor = d.supervisor


def trade_supervisors(void):
    """Trade supervisors (e.g. electrical) linked to this void by its trades or flags."""
    sups = list(Supervisor.objects.filter(active=True, trade__isnull=False).select_related("trade"))
    if not sups:
        return []
    needed = {t.trade_id for t in void.tasks.all() if t.status != S.NOT_REQUIRED}
    out = []
    for s in sups:
        if s.trade_id in needed or (void.electrical_flag and s.trade.name.lower() == "electrical"):
            out.append(s)
    return out


def derive_status(void):
    """Work out the overall status (the row colour) from the trades."""
    if void.status_fixed or void.status == Void.Status.HANDED_BACK:
        return void.status
    statuses = [t.status for t in void.tasks.all() if t.status != S.NOT_REQUIRED]
    if not statuses:
        return Void.Status.NEW if void.status == Void.Status.NEW else Void.Status.STC
    if all(s == S.COMPLETE for s in statuses):
        return Void.Status.COMPLETE
    if all(s in (S.COMPLETE, S.SNAGGING) for s in statuses):
        return Void.Status.SNAGGING
    if all(s == S.STC for s in statuses):
        return Void.Status.STC
    return Void.Status.IN_PROGRESS


def refresh_status(void, actor=None, activity=True):
    """Re-work the overall status. `activity` is False for imports, which don't count as progress."""
    new = derive_status(void)
    if new != void.status:
        old = void.status
        void.status = new
        void.save(update_fields=["status", "updated_at"])
        record(actor, "void.status", void, f"{void} status: {void.get_status_display()}",
               {"status": {"from": old, "to": new}})
        if activity:
            add_update(void, actor, VoidUpdate.Kind.STATUS, f"Status → {void.get_status_display()}")


def touch(void):
    """Mark real progress on a void (resets the 'needs an update' flag)."""
    void.last_activity = timezone.now()
    Void.objects.filter(pk=void.pk).update(last_activity=void.last_activity)


def add_update(void, author, kind, text):
    """Add a job diary entry. Every diary entry counts as progress."""
    upd = VoidUpdate.objects.create(void=void, author=author if getattr(author, "pk", None) else None,
                                    kind=kind, text=text.strip())
    touch(void)
    if kind == VoidUpdate.Kind.ISSUE:
        # An Issue update raises an issue on the whole job until someone clears it.
        void.issue_flag, void.issue_note = True, text.strip()[:300]
        Void.objects.filter(pk=void.pk).update(issue_flag=True, issue_note=void.issue_note)
    if kind == VoidUpdate.Kind.VISIT:
        void.last_inspection = timezone.localdate()
        fields = ["last_inspection"]
        if void.status == Void.Status.NEW:
            void.status = Void.Status.STC  # visited: no longer "needs visit"
            fields.append("status")
        Void.objects.filter(pk=void.pk).update(**{f: getattr(void, f) for f in fields})
        refresh_status(void, author)
    return upd


def trade_update_text(task):
    bits = [task.assignee, task.date.strftime("%d/%m") if task.date else ""]
    who = ", ".join(b for b in bits if b)
    text = f"{task.trade.name} → {task.get_status_display() if task.status else 'cleared'}"
    if who:
        text += f" ({who})"
    if task.claimed:
        text += " · claimed"
    return text


def changed_fields(before, after, fields):
    out = {}
    for f in fields:
        a, b = getattr(before, f, None), getattr(after, f, None)
        if a != b:
            out[f] = {"from": str(a) if a is not None else None, "to": str(b) if b is not None else None}
    return out


def needs_electrical(void):
    return void.electrical_flag or any(
        t.trade.name.lower() == "electrical" and t.status != S.NOT_REQUIRED for t in void.tasks.all())


def simpro_number(void):
    """Sort key: the first number in the SIMPRO field (blank last)."""
    import re
    m = re.search(r"\d+", void.simpro or "")
    return (0, int(m.group())) if m else (1, 0)
