"""Canonical vocabulary shared by the loader and the detector.

Two facts drive everything here:

* The dataset stores the region as a Chinese display name
  (``ccf-aiops-西安``) while the submission schema wants the latin city slug
  (``xian-service-vm-2``). The mapping is by substring, because the exact
  prefix is not stable across the sample bundle and the full dataset.
* ``monitor-vm`` is a valid member of ``VALID_NETWORK_ELEMENTS`` in the public
  schema, so nothing rejects a prediction naming it. It is still a collector,
  not a fault domain, so we exclude it ourselves rather than relying on
  validation.
"""

from __future__ import annotations

# Latin slug -> every substring that identifies that region in a raw row.
# Order matters only for readability; lookup scans for the first hit and the
# slugs are mutually exclusive.
REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "beida": ("beida", "北大", "bei-da"),
    "shenyang": ("shenyang", "沈阳"),
    "xian": ("xian", "西安"),
    "chengdu": ("chengdu", "成都"),
    "wuhan": ("wuhan", "武汉"),
    "shanghai": ("shanghai", "上海"),
    "nanjing": ("nanjing", "南京"),
    "guangzhou": ("guangzhou", "广州"),
}

CITIES: tuple[str, ...] = (
    "beida",
    "shenyang",
    "xian",
    "chengdu",
    "wuhan",
    "shanghai",
    "nanjing",
    "guangzhou",
)

# The ten device roles that make up a valid ``network_element_id``.
DEVICE_ROLES: tuple[str, ...] = (
    "br-1",
    "br-2",
    "cr-1",
    "cr-2",
    "fw",
    "traffic-vm",
    "service-vm-1",
    "service-vm-2",
    "service-vm-3",
    "monitor-vm",
)

# Roles that may not be reported as a root cause. monitor-vm is a telemetry
# collector; probe-vm only exists in the sample bundle.
EXCLUDED_ROLES: frozenset[str] = frozenset({"monitor-vm", "probe-vm", "collector", "probe"})

# Raw ``node_type`` / raw ``node`` token -> canonical role.
# The sample bundle uses generic names (``service``, ``cr``) while the full
# dataset uses the suffixed roles directly, so both spellings have to land on
# the same canonical value.
ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "br-1": ("br-1", "br1"),
    "br-2": ("br-2", "br2"),
    "cr-1": ("cr-1", "cr1"),
    "cr-2": ("cr-2", "cr2"),
    "fw": ("fw", "firewall"),
    "traffic-vm": ("traffic-vm", "traffic_vm", "traffic"),
    "service-vm-1": ("service-vm-1", "service_vm_1", "service-1", "service_1"),
    "service-vm-2": ("service-vm-2", "service_vm_2", "service-2", "service_2"),
    "service-vm-3": ("service-vm-3", "service_vm_3", "service-3", "service_3"),
    "monitor-vm": ("monitor-vm", "monitor_vm", "monitor", "collector"),
    "probe-vm": ("probe-vm", "probe_vm", "probe"),
}

# Generic node names that appear bare when the suffix is only encoded in
# ``node_type``. Resolved by consulting ``node_type`` before falling back.
_BARE_ROLE = {
    "service": "service-vm",
    "cr": "cr",
    "br": "br",
    "traffic": "traffic-vm",
    "firewall": "fw",
    "collector": "monitor-vm",
    "probe": "probe-vm",
}


def region_of(text: str) -> str | None:
    """Return the canonical city slug for a raw region/node string."""
    lowered = text.lower()
    for slug, aliases in REGION_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return slug
    return None


def role_of(node: str, node_type: str = "") -> str | None:
    """Return the canonical device role for a row's node columns.

    ``node`` carries the full role in the phase-1 dataset (``service-vm-2``)
    but only the family in the sample bundle (``service``), where the trailing
    digit lives in ``node_type``. We try the specific spellings first so
    ``service-vm-2`` never collapses onto ``service-vm-1``.
    """
    raw_node = (node or "").strip().strip('"').lower()
    raw_type = (node_type or "").strip().strip('"').lower()

    # Exact or suffixed matches on the node column, most specific first.
    for role in DEVICE_ROLES:
        for alias in ROLE_ALIASES[role]:
            if raw_node == alias:
                return role
    for role in DEVICE_ROLES:
        # Deliberately skip the bare family aliases here: a prefix hit on
        # "service" must not shadow a later, more specific role.
        if role in raw_node:
            return role

    # Fall back to the node_type family, then to the raw node text itself.
    for token, family in _BARE_ROLE.items():
        if raw_type == token or raw_node == token:
            if family in ("cr", "br"):
                # "cr" / "br" without a suffix is genuinely ambiguous; the
                # sample bundle uses cr-1/br-1 for its single instance.
                return f"{family}-1"
            return family
    for role in DEVICE_ROLES:
        if role in raw_type:
            return role
    return None


def element_id(region: str, role: str) -> str:
    return f"{region}-{role}"


def is_candidate_role(role: str) -> bool:
    return role in DEVICE_ROLES and role not in EXCLUDED_ROLES
