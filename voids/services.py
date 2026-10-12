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
    if void.status == Void.Status.COMPLETE and not void.final_check_requested_at and activity:
        request_final_check(void, actor)
    elif void.status not in (Void.Status.COMPLETE, Void.Status.HANDED_BACK) and void.final_check_requested_at:
        # Reopened (e.g. a trade back in progress): ask again next time it is complete.
        void.final_check_requested_at = None
        Void.objects.filter(pk=void.pk).update(final_check_requested_at=None)


def request_final_check(void, actor=None):
    from .notify import send_final_check_request
    sent = send_final_check_request(void)
    void.final_check_requested_at = timezone.now()
    Void.objects.filter(pk=void.pk).update(final_check_requested_at=void.final_check_requested_at)
    who = ", ".join(sent) if sent else "no supervisor email set up – nobody emailed"
    add_update(void, None, VoidUpdate.Kind.STATUS, f"All trades complete: final check requested ({who})")


def confirm_final_check(void, user):
    """The supervisor has final checked the property: the void moves to Completed."""
    void.status = Void.Status.HANDED_BACK
    void.final_checked_at = timezone.now()
    void.final_checked_by = user
    void.handed_back_at = timezone.localdate()
    void.issue_flag = False
    void.save(update_fields=["status", "final_checked_at", "final_checked_by", "handed_back_at", "issue_flag",
                             "updated_at"])
    record(user, "void.final_check", void, f"Final check confirmed for {void}")
    add_update(void, user, VoidUpdate.Kind.STATUS, "Final check confirmed – moved to Completed")


def reopen(void, user):
    void.status = Void.Status.IN_PROGRESS
    void.status_fixed = False
    void.final_checked_at = None
    void.final_checked_by = None
    void.handed_back_at = None
    void.save(update_fields=["status", "status_fixed", "final_checked_at", "final_checked_by", "handed_back_at",
                             "updated_at"])
    record(user, "void.reopen", void, f"Reopened {void}")
    add_update(void, user, VoidUpdate.Kind.STATUS, "Reopened (taken out of Completed)")
    refresh_status(void, user)


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


def log_trade_change(void, user, task, comment):
    """Diary line for a trade change. Quick successive edits by the same person to the same trade
    (e.g. status, then who, then date) update one line instead of adding several."""
    text = trade_update_text(task)
    if comment:
        text = f"{text}: {comment}"
    last = void.updates.filter(hidden=False, kind=VoidUpdate.Kind.TRADE,
                               created_at__gte=timezone.now() - timezone.timedelta(minutes=10)).first()
    if (last and last.author_id == getattr(user, "pk", None) and not comment
            and last.text.startswith(task.trade.name + " →")):
        last.text = text
        last.save(update_fields=["text"])
        touch(void)
        return last
    return add_update(void, user, VoidUpdate.Kind.TRADE, text)
