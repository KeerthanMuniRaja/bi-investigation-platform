"""
One place that knows how to build a connector from a saved connection.
Adding a platform = add a branch here + a connector class; nothing in
app/engine or app/api changes (Section 33).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from app.connectors.base import BIConnector
from app.connectors.files import ExportFile, FileExportConnector
from app.connectors.model_file import ModelFileConnector
from app.connectors.pbip import PbipConnector
from app.connectors.powerbi import PowerBIConnector
from app.connectors.tableau import TableauConnector
from app.models.graph import utcnow

Platform = Literal["pbix", "powerbi", "pbip", "tableau", "files"]

PLATFORMS = [
    {"id": "pbix", "name": "Power BI file (.pbix / .pbit / .bim / PBIP zip)",
     "description": "Upload the report file itself. The semantic model, Power Query sources, pages and visuals are read "
                    "straight from it — no exports, no credentials.",
     "fields": ["model_file"]},
    {"id": "files", "name": "Exported files (any BI tool)",
     "description": "Upload Excel/CSV exports from Power BI, Tableau, Qlik, Looker, SSRS… Each tab is a page.",
     "fields": ["file_ids", "source_platform"]},
    {"id": "powerbi", "name": "Power BI (live)",
     "description": "Service principal + Execute Queries. Add a PBIP export folder for visual-level data.",
     "fields": ["tenant_id", "client_id", "client_secret", "workspace_id", "pbip_dir"]},
    {"id": "pbip", "name": "Power BI project (PBIP folder)",
     "description": "Offline: full lineage and impact from a PBIP/TMDL folder, no tenant access needed.",
     "fields": ["pbip_dir"]},
    {"id": "tableau", "name": "Tableau Server / Cloud",
     "description": "Personal access token. View data via REST, lineage via the Metadata API.",
     "fields": ["server_url", "site", "pat_name", "pat_secret", "api_version"]},
]

SECRET_FIELDS = ("client_secret", "pat_secret")


class ConnectionRecord(BaseModel):
    """Persisted connection settings. Secrets are never stored here — they
    live in process memory (or come from environment variables)."""

    id: str
    name: str
    platform: Platform
    workspace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    pbip_dir: Optional[str] = None
    server_url: Optional[str] = None
    site: Optional[str] = None
    pat_name: Optional[str] = None
    api_version: str = "3.21"
    file_ids: list[str] = Field(default_factory=list)
    source_platform: str = "generic"
    synced_workspaces: list[str] = Field(default_factory=list)
    last_synced_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())

    @property
    def mode(self) -> str:
        return "file" if self.platform in ("files", "pbip", "pbix") else "live"


def build_connector(
    record: ConnectionRecord,
    secrets: dict[str, str],
    resolve_file: Callable[[str], tuple[str, Path]],
) -> BIConnector:
    if record.platform == "pbix":
        if not record.file_ids:
            raise ValueError("No Power BI file attached to this connection — upload it on the Power BI models page.")
        name, path = resolve_file(record.file_ids[0])
        return ModelFileConnector(path, display_name=name)
    if record.platform == "files":
        files = []
        for file_id in record.file_ids:
            name, path = resolve_file(file_id)
            files.append(ExportFile(file_id=file_id, name=name, path=path))
        return FileExportConnector(files, source_platform=record.source_platform)
    if record.platform == "pbip":
        if not record.pbip_dir:
            raise ValueError("pbip_dir is required for a PBIP connection")
        return PbipConnector(Path(record.pbip_dir))
    if record.platform == "powerbi":
        return PowerBIConnector(
            tenant_id=record.tenant_id,
            client_id=record.client_id,
            client_secret=secrets.get("client_secret"),
            default_workspace_id=record.workspace_id,
            pbip_export_dir=Path(record.pbip_dir) if record.pbip_dir else None,
        )
    if record.platform == "tableau":
        import os

        return TableauConnector(
            server_url=record.server_url or os.environ.get("TABLEAU_SERVER_URL", ""),
            site_content_url=record.site or os.environ.get("TABLEAU_SITE", ""),
            pat_name=record.pat_name or os.environ.get("TABLEAU_PAT_NAME"),
            pat_secret=secrets.get("pat_secret") or os.environ.get("TABLEAU_PAT_SECRET"),
            api_version=record.api_version,
        )
    raise ValueError(f"Unknown platform '{record.platform}'")
