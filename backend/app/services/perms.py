"""«Права» (Stanislav 11-23): what a person may do in a section — Ч read, С create, И update, У delete. Every standard
role has its standard rights (SECTIONS below = exactly the code's behaviour before «Права»); the superadmin can change
them for one person in his card (Управление → сотрудник), e.g. Irakli's «оператор, который только выкладывает фото» =
an operator with «Договоры» off. users.perms keeps only the sections changed for him; a new role drops them.
A section moves here from require_level / at_least one at a time.

superadmin always has every right; daily_rent_manager and car_operator have none here (api/deps.current_user keeps
them inside their own sections)."""
from app.models import User
from app.services.errors import DomainError, forbidden

ACTIONS = "rcud"
ROLES = ("admin", "operator", "agent", "recruit")  # the roles whose rights can be changed per person
_STAFF = ("operator", "admin")

# key → (label, the actions the section has, role → its standard actions)
SECTIONS: dict[str, tuple[str, str, dict[str, str]]] = {
    # api/contracts.py: read = list, card, PDF; create = the wizard (draft, new); update = everything after (edit, sign,
    # link, e-sign, close, share, send). Which contracts one sees (own / all) stays in services/contracts.py.
    "contracts": ("Договоры", "rcu", {r: "rcu" for r in ROLES}),  # recruits too (Irakli 11-22)
    # GET /properties/{id}/originals: the photos without the watermark, for myhome / ss by hand (Irakli 11-22)
    "originals": ("Скачать оригиналы", "r", {r: "r" for r in _STAFF}),
}


def defaults(role: str) -> dict[str, str]:
    """The standard rights of a role: section → actions."""
    return {s: has if role == "superadmin" else d.get(role, "") for s, (_, has, d) in SECTIONS.items()}


def for_user(u: User) -> dict[str, str]:
    """section → his actions: the role's, with his own changes on top (/auth/me «perms», the frontend's can())."""
    out = defaults(u.role)
    if u.role in ROLES:
        out.update({s: a for s, a in (u.perms or {}).items() if s in out})
    return out


def can(u: User | None, section: str, action: str) -> bool:
    assert section in SECTIONS and action in ACTIONS, (section, action)
    return u is not None and action in for_user(u)[section]


def check(u: User, section: str, action: str) -> None:
    if not can(u, section, action):
        raise DomainError("forbidden", f"no right «{action}» in «{SECTIONS[section][0]}»")


def clean(actor: User | None, role: str, body: dict | None) -> dict | None:
    """users.perms from the form: superadmin only; only the sections that differ from the role's, None = as the role.
    The change lands in the user's «updated» event (services/users.update) — the rights history."""
    if actor is None or actor.role != "superadmin":
        raise forbidden("only a superadmin changes a person's rights")
    body = body or {}
    if bad := set(body) - set(SECTIONS):
        raise DomainError("bad_section", f"unknown section {', '.join(sorted(bad))}")
    if role not in ROLES:
        return None  # superadmin: everything; Daily Rent / car operators: their sections (the form sends theirs as is)
    std = defaults(role)
    out = {s: "".join(a for a in SECTIONS[s][1] if a in (acts or "")) for s, acts in body.items()}
    return {s: a for s, a in out.items() if a != std[s]} or None


def info() -> dict:
    """GET /admin/permissions: the sections and every role's standard rights, for the checkboxes in the card."""
    return {"sections": [{"key": k, "label": v[0], "actions": v[1]} for k, v in SECTIONS.items()],
            "roles": list(ROLES), "defaults": {r: defaults(r) for r in ROLES}}
