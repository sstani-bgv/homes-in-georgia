"""Role × section rights (the «Права» tab in Управление, Stanislav 11-23): what each role may do in a section — Ч read,
С create, И update, У delete — as flags the superadmin sets, instead of a role ladder in the code. The defaults below are
exactly the code's behaviour before the matrix; a section moves here from require_level / at_least one at a time.

superadmin always has every right (nobody locks the matrix's own editor out); daily_rent_manager and car_operator are
not in the matrix: api/deps.current_user keeps them inside their own sections whatever the flags say.

Custom roles («оператор, который только выкладывает фото», Irakli 11-22): the superadmin makes one on the basis of a
ladder role and takes flags off. Its users keep users.role = the base (every check not yet in the matrix sees the base);
the matrix row is «c<id>», used while the base still equals users.role (a promoted recruit falls back to his new role)."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CustomRole, RolePermission, User
from app.services.common import log_event
from app.services.errors import DomainError, forbidden

ACTIONS = "rcud"
MATRIX_ROLES = ("admin", "operator", "agent", "recruit")
_LADDER = ("recruit", "agent", "operator", "admin")
_STAFF = ("operator", "admin")

# key → (label, the actions the section has, role → its default actions)
SECTIONS: dict[str, tuple[str, str, dict[str, str]]] = {
    # api/contracts.py: read = list, card, PDF; create = the wizard (draft, new); update = everything after (edit, sign,
    # link, e-sign, close, share, send). Which contracts one sees (own / all) stays in services/contracts.py.
    "contracts": ("Договоры", "rcu", {r: "rcu" for r in _LADDER}),  # recruits too (Irakli 11-22)
    # GET /properties/{id}/originals: the photos without the watermark, for myhome / ss by hand (Irakli 11-22)
    "originals": ("Скачать оригиналы", "r", {r: "r" for r in _STAFF}),
}


def _key(cr: CustomRole) -> str:
    return f"c{cr.id}"


def _stored(db: Session, role: str | None = None) -> dict[tuple[str, str], str]:
    q = select(RolePermission)
    if role:
        q = q.where(RolePermission.role == role)
    return {(p.role, p.section): p.actions for p in db.scalars(q)}


def _cell(stored: dict, role: str, section: str, base: str | None = None) -> str:
    """base: the ladder role a custom role «c<id>» is made on (its default)."""
    _, has, defaults = SECTIONS[section]
    if role == "superadmin":
        return has
    if (base or role) not in MATRIX_ROLES:
        return ""
    v = stored.get((role, section))
    return "".join(a for a in has if a in (defaults.get(base or role, "") if v is None else v))


def _row(db: Session, user: User) -> tuple[str, str | None]:
    """The user's matrix row: (role key, base) — his custom role if it is still of his role."""
    cr = db.get(CustomRole, user.custom_role_id) if user.custom_role_id else None
    return (_key(cr), cr.base) if cr and cr.base == user.role else (user.role, None)


def can(db: Session, user: User | None, section: str, action: str) -> bool:
    assert section in SECTIONS and action in ACTIONS, (section, action)
    if user is None:
        return False
    role, base = _row(db, user)
    return action in _cell(_stored(db, role), role, section, base)


def check(db: Session, user: User, section: str, action: str) -> None:
    if not can(db, user, section, action):
        raise DomainError("forbidden", f"no right «{action}» in «{SECTIONS[section][0]}»")


def for_user(db: Session, user: User) -> dict[str, str]:
    """/auth/me «perms»: section → the user's actions, what the frontend's can() reads."""
    role, base = _row(db, user)
    stored = _stored(db, role)
    return {s: _cell(stored, role, s, base) for s in SECTIONS}


def _rows(db: Session) -> list[dict]:
    """The matrix rows: the ladder roles, then the custom ones (key, label, base)."""
    return [{"key": r, "label": None, "base": None} for r in MATRIX_ROLES] + [
        {"key": _key(c), "id": c.id, "label": c.label, "base": c.base,
         "users": db.scalar(select(User.id).where(User.custom_role_id == c.id, User.role == c.base, User.deleted_at.is_(None)).limit(1)) is not None}
        for c in db.scalars(select(CustomRole).order_by(CustomRole.id))]


def matrix(db: Session) -> dict:
    stored, rows = _stored(db), _rows(db)
    return {"sections": [{"key": k, "label": v[0], "actions": v[1]} for k, v in SECTIONS.items()],
            "roles": [r["key"] for r in rows if not r["base"]],
            "custom": [r for r in rows if r["base"]],
            "matrix": {r["key"]: {s: _cell(stored, r["key"], s, r["base"]) for s in SECTIONS} for r in rows},
            "defaults": {r["key"]: {s: _cell({}, r["key"], s, r["base"]) for s in SECTIONS} for r in rows}}


def save(db: Session, body: dict[str, dict[str, str]], actor: User) -> dict:
    """body: role key → section → actions (only the roles / sections sent change). A cell equal to its default is stored
    as no row, so a later change of the default reaches it; every change is an event («permissions», role key)."""
    stored = _stored(db)
    bases = {r["key"]: r["base"] for r in _rows(db)}
    for role, cells in body.items():
        if role not in bases:
            raise DomainError("bad_role", f"role {role!r} is not in the matrix")
        changes = {}
        for section, acts in cells.items():
            if section not in SECTIONS:
                raise DomainError("bad_section", f"unknown section {section!r}")
            has = SECTIONS[section][1]
            new = "".join(a for a in has if a in (acts or ""))
            old = _cell(stored, role, section, bases[role])
            if new == old:
                continue
            changes[section] = {"from": old, "to": new}
            row = db.get(RolePermission, (role, section))
            if new == _cell({}, role, section, bases[role]):
                if row:
                    db.delete(row)
            elif row:
                row.actions = new
            else:
                db.add(RolePermission(role=role, section=section, actions=new))
        if changes:
            log_event(db, "permissions", role, "updated", actor, changes=changes)
    db.commit()
    return matrix(db)


def create_role(db: Session, label: str, base: str, actor: User) -> dict:
    """«Новая роль на основе …»: the base's current flags copied, so the new row starts as the base does today."""
    label = (label or "").strip()[:60]
    if not label:
        raise DomainError("bad_input", "Название роли не может быть пустым")
    if base not in MATRIX_ROLES:
        raise DomainError("bad_role", f"a role is made on the basis of {', '.join(MATRIX_ROLES)}")
    cr = CustomRole(label=label, base=base)
    db.add(cr)
    db.flush()
    stored = _stored(db, base)
    for s in SECTIONS:
        if (acts := _cell(stored, base, s)) != _cell({}, base, s):
            db.add(RolePermission(role=_key(cr), section=s, actions=acts))
    log_event(db, "permissions", _key(cr), "role_created", actor, role_label=label, base=base)
    db.commit()
    return matrix(db)


def delete_role(db: Session, cid: int, actor: User) -> dict:
    """Its users go back to their role's flags (users.custom_role_id → NULL by the foreign key)."""
    cr = db.get(CustomRole, cid)
    if cr is None:
        raise DomainError("not_found", "Роль не найдена", 404)
    db.execute(delete(RolePermission).where(RolePermission.role == _key(cr)))
    log_event(db, "permissions", _key(cr), "role_deleted", actor, role_label=cr.label, base=cr.base)
    db.delete(cr)
    db.commit()
    return matrix(db)


def check_assign(db: Session, actor: User | None, cid: int | None, role: str) -> None:
    """users.custom_role_id: set by the superadmin only, a custom role of the user's own role."""
    if actor is None or actor.role != "superadmin":
        raise forbidden("only a superadmin sets a user's custom role")
    if cid is not None and ((cr := db.get(CustomRole, cid)) is None or cr.base != role):
        raise DomainError("bad_role", "Эта роль сделана на основе другой роли")
