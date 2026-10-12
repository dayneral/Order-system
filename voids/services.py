"""Rules for voids: area and supervisor from the postcode, and the overall status."""

from audit.models import record

from .models import PostcodeDistrict, Supervisor, TradeTask, Void, district_of

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
    needed = {t.trade_id for t in void.tasks.all() if t.status not in ("", S.NOT_REQUIRED)}
    out = []
    for s in sups:
        if s.trade_id in needed or (void.electrical_flag and s.trade.name.lower() == "electrical"):
            out.append(s)
    return out


def derive_status(void):
    """Work out the overall status (the row colour) from the trades."""
    if void.status_fixed or void.status == Void.Status.HANDED_BACK:
        return void.status
    statuses = [t.status for t in void.tasks.all() if t.status not in ("", S.NOT_REQUIRED)]
    if not statuses:
        return Void.Status.NEW if void.status == Void.Status.NEW else Void.Status.STC
    if all(s == S.COMPLETE for s in statuses):
        return Void.Status.COMPLETE
    if all(s in (S.COMPLETE, S.SNAGGING) for s in statuses):
        return Void.Status.SNAGGING
    if all(s == S.STC for s in statuses):
        return Void.Status.STC
    return Void.Status.IN_PROGRESS


def refresh_status(void, actor=None):
    new = derive_status(void)
    if new != void.status:
        old = void.status
        void.status = new
        void.save(update_fields=["status", "updated_at"])
        record(actor, "void.status", void, f"{void} status: {void.get_status_display()}",
               {"status": {"from": old, "to": new}})


def changed_fields(before, after, fields):
    out = {}
    for f in fields:
        a, b = getattr(before, f, None), getattr(after, f, None)
        if a != b:
            out[f] = {"from": str(a) if a is not None else None, "to": str(b) if b is not None else None}
    return out
