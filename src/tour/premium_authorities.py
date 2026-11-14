"""Packaged hashes for the frozen Premium authoring authorities.

The two manifests these last two hashes are taken from live under
``fixtures/tour-certification/``.

The reference manifest lists 28 documents. Only the THREE that the CALIBRATION
manifest's ``anchors`` point at are ever opened, and those three live under
``fixtures/certification-references/``. (The reference manifest separately
classifies four entries as ``calibration_anchor``; that field is not what the
loader reads.) The other 25 resolve to nothing. The manifest is a sealed
provenance record, not a promise that every file it names still exists.

Runtime code imports only the pins below, so a manifest that moves does not
change what runs — but no test holds a pin to its manifest, so a pin that no
longer matches its manifest goes unnoticed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PremiumAuthorityHashes:
    contract_sha256: str
    reference_manifest_sha256: str
    calibration_manifest_sha256: str


PREMIUM_AUTHORITIES = PremiumAuthorityHashes(
    contract_sha256="a7250bada7cb09ad9a47e159b9a89f6385046555dbd3e5d99ccf31d581f3a82d",
    reference_manifest_sha256="dfb7cc205422473dad2e3c168c471aa5c4060c73c2cc450be0665ead6db98378",
    calibration_manifest_sha256="d4548864786d11ec44a00c2bfd753cc9d42909bdda511c819f144efe1646f56a",
)


__all__ = ["PREMIUM_AUTHORITIES", "PremiumAuthorityHashes"]
