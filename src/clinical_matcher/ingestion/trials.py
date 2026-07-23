import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import CriterionType
from ..splits import current_git_commit
from ..validation import validate_document


TRIAL_PROTOCOL_VERSION = "1.0.0"
TRIAL_PROTOCOL_SCHEMA_RESOURCE = "schemas/trial-protocol-1.0.0.schema.json"
CLINICALTRIALS_API_BASE = "https://clinicaltrials.gov/api/v2"
CLINICALTRIALS_TERMS_URL = (
    "https://clinicaltrials.gov/about-site/terms-conditions"
)
NCT_PATTERN = re.compile(r"^NCT[0-9]{8}$")
HEADING_PATTERN = re.compile(
    r"^[ \t]*(?:\*\*)?(?:key[ \t]+)?"
    r"(inclusion|exclusion)[ \t]+criteria[ \t]*:?"
    r"[ \t]*(?:\*\*)?[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)
BULLET_PATTERN = re.compile(
    r"^[ \t]*(?:[-*•]|[0-9]+[.)])[ \t]+",
    re.MULTILINE,
)


class TrialImportError(ValueError):
    """Raised when a public study cannot be normalized without guessing."""

    def __init__(self, message: str, code: str = "unexpected_import_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProtocolCriterion:
    criterion_id: str
    criterion_type: CriterionType
    source_id: str
    source_span_start: int
    source_span_end: int
    source_text: str
    normalized_text: str


@dataclass(frozen=True)
class TrialProtocol:
    nct_id: str
    title: str
    source_url: str
    source_id: str
    source_record_version: str
    registry_snapshot_date: str
    last_update_posted: str
    api_version: str
    api_data_timestamp: str
    retrieved_at: str
    importer_code_commit: str
    eligibility_text: str
    eligibility_sha256: str
    sex: Optional[str]
    minimum_age: Optional[str]
    maximum_age: Optional[str]
    healthy_volunteers: Optional[bool]
    standard_ages: Tuple[str, ...]
    criteria: Tuple[ProtocolCriterion, ...]
    modifications: Tuple[str, ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trimmed_span(text: str, start: int, end: int) -> Tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _normalized_criterion(text: str) -> str:
    unescaped = re.sub(r"\\([<>])", r"\1", text)
    return " ".join(unescaped.split())


def _section_item_spans(
    eligibility_text: str,
    section_start: int,
    section_end: int,
) -> Tuple[Tuple[int, int], ...]:
    section = eligibility_text[section_start:section_end]
    bullets = tuple(BULLET_PATTERN.finditer(section))
    spans = []
    if bullets:
        for index, bullet in enumerate(bullets):
            start = section_start + bullet.end()
            end = (
                section_start + bullets[index + 1].start()
                if index + 1 < len(bullets)
                else section_end
            )
            start, end = _trimmed_span(eligibility_text, start, end)
            if start < end:
                spans.append((start, end))
        return tuple(spans)

    paragraph_pattern = re.compile(
        r"\S(?:.*?)(?=\r?\n[ \t]*\r?\n|\Z)",
        re.DOTALL,
    )
    for paragraph in paragraph_pattern.finditer(section):
        start = section_start + paragraph.start()
        end = section_start + paragraph.end()
        start, end = _trimmed_span(eligibility_text, start, end)
        if start < end:
            spans.append((start, end))
    return tuple(spans)


def parse_eligibility_criteria(
    nct_id: str,
    source_id: str,
    eligibility_text: str,
) -> Tuple[ProtocolCriterion, ...]:
    if not eligibility_text.strip():
        raise TrialImportError(
            "Eligibility criteria are empty",
            code="empty_eligibility",
        )
    headings = tuple(HEADING_PATTERN.finditer(eligibility_text))
    if not headings:
        raise TrialImportError(
            "Eligibility headings are absent; criterion polarity would be "
            "ambiguous",
            code="ambiguous_polarity",
        )

    criteria: List[ProtocolCriterion] = []
    duplicate_counts: Dict[str, int] = {}
    for index, heading in enumerate(headings):
        criterion_type = CriterionType(heading.group(1).lower())
        section_start = heading.end()
        section_end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(eligibility_text)
        )
        for start, end in _section_item_spans(
            eligibility_text,
            section_start,
            section_end,
        ):
            source_text = eligibility_text[start:end]
            normalized_text = _normalized_criterion(source_text)
            if not normalized_text:
                continue
            digest = hashlib.sha256(
                (
                    f"{nct_id}|{criterion_type.value}|{normalized_text}"
                ).encode("utf-8")
            ).hexdigest()[:16]
            base_id = (
                f"{nct_id.lower()}-{criterion_type.value}-{digest}"
            )
            duplicate_counts[base_id] = duplicate_counts.get(base_id, 0) + 1
            count = duplicate_counts[base_id]
            criterion_id = base_id if count == 1 else f"{base_id}-{count}"
            criteria.append(
                ProtocolCriterion(
                    criterion_id=criterion_id,
                    criterion_type=criterion_type,
                    source_id=source_id,
                    source_span_start=start,
                    source_span_end=end,
                    source_text=source_text,
                    normalized_text=normalized_text,
                )
            )
    if not criteria:
        raise TrialImportError(
            "No criteria could be extracted",
            code="no_extractable_criteria",
        )
    return tuple(criteria)


def trial_protocol_document(protocol: TrialProtocol) -> Dict[str, Any]:
    document = {
        "protocol_version": TRIAL_PROTOCOL_VERSION,
        "registry": "ClinicalTrials.gov",
        "attribution": (
            "ClinicalTrials.gov, U.S. National Library of Medicine"
        ),
        "terms_url": CLINICALTRIALS_TERMS_URL,
        **asdict(protocol),
    }
    document["standard_ages"] = list(protocol.standard_ages)
    document["criteria"] = [
        {
            **asdict(criterion),
            "criterion_type": criterion.criterion_type.value,
            "source_span": {
                "start": criterion.source_span_start,
                "end": criterion.source_span_end,
            },
        }
        for criterion in protocol.criteria
    ]
    for criterion in document["criteria"]:
        criterion.pop("source_span_start")
        criterion.pop("source_span_end")
    document["modifications"] = list(protocol.modifications)
    validate_document(document, TRIAL_PROTOCOL_SCHEMA_RESOURCE)
    return document


def normalize_study(
    study: Dict[str, Any],
    version_payload: Dict[str, Any],
    retrieved_at: Optional[str] = None,
    importer_code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    protocol_section = study.get("protocolSection", {})
    identification = protocol_section.get("identificationModule", {})
    status = protocol_section.get("statusModule", {})
    eligibility = protocol_section.get("eligibilityModule", {})
    misc = study.get("derivedSection", {}).get("miscInfoModule", {})

    nct_id = identification.get("nctId")
    if not isinstance(nct_id, str) or not NCT_PATTERN.fullmatch(nct_id):
        raise TrialImportError(
            "Study has no valid NCT identifier",
            code="invalid_nct_id",
        )
    title = identification.get("briefTitle")
    if not isinstance(title, str) or not title.strip():
        raise TrialImportError(
            "Study has no brief title",
            code="missing_title",
        )
    eligibility_text = eligibility.get("eligibilityCriteria")
    if not isinstance(eligibility_text, str):
        raise TrialImportError(
            "Study has no eligibility criteria text",
            code="missing_eligibility",
        )
    registry_snapshot_date = misc.get("versionHolder")
    last_update = status.get("lastUpdatePostDateStruct", {}).get("date")
    api_version = version_payload.get("apiVersion")
    data_timestamp = version_payload.get("dataTimestamp")
    required_strings = {
        "registry snapshot date": registry_snapshot_date,
        "last update posted date": last_update,
        "API version": api_version,
        "API data timestamp": data_timestamp,
    }
    missing = [
        label
        for label, value in required_strings.items()
        if not isinstance(value, str) or not value
    ]
    if missing:
        raise TrialImportError(
            f"Study provenance is incomplete: {', '.join(missing)}",
            code="incomplete_provenance",
        )

    eligibility_sha256 = hashlib.sha256(
        eligibility_text.encode("utf-8")
    ).hexdigest()
    source_record_version = f"{last_update}:{eligibility_sha256[:12]}"
    source_id = (
        f"clinicaltrials.gov:{nct_id}:eligibility:{source_record_version}"
    )
    criteria = parse_eligibility_criteria(
        nct_id=nct_id,
        source_id=source_id,
        eligibility_text=eligibility_text,
    )
    protocol = TrialProtocol(
        nct_id=nct_id,
        title=title.strip(),
        source_url=f"https://clinicaltrials.gov/study/{nct_id}",
        source_id=source_id,
        source_record_version=source_record_version,
        registry_snapshot_date=registry_snapshot_date,
        last_update_posted=last_update,
        api_version=api_version,
        api_data_timestamp=data_timestamp,
        retrieved_at=retrieved_at or _now(),
        importer_code_commit=importer_code_commit or current_git_commit(),
        eligibility_text=eligibility_text,
        eligibility_sha256=eligibility_sha256,
        sex=eligibility.get("sex"),
        minimum_age=eligibility.get("minimumAge"),
        maximum_age=eligibility.get("maximumAge"),
        healthy_volunteers=eligibility.get("healthyVolunteers"),
        standard_ages=tuple(eligibility.get("stdAges", ())),
        criteria=criteria,
        modifications=(
            "Split eligibility markup by explicit inclusion/exclusion headings.",
            "Removed list markers from criterion source spans.",
            "Collapsed whitespace in normalized_text.",
            "Unescaped ClinicalTrials.gov comparison markup in normalized_text.",
            "Preserved the complete source eligibility text and character spans.",
        ),
    )
    return trial_protocol_document(protocol)


class ClinicalTrialsClient:
    def __init__(
        self,
        api_base: str = CLINICALTRIALS_API_BASE,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _get_json(self, url: str) -> Dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "ClinicalMatcher/0.1 "
                    "(https://github.com/Charlie-Uni/ClinicalMatcher)"
                ),
            },
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise TrialImportError(
                "ClinicalTrials.gov returned non-object JSON",
                code="invalid_api_response",
            )
        return payload

    def fetch(self, nct_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        normalized_id = nct_id.upper()
        if not NCT_PATTERN.fullmatch(normalized_id):
            raise TrialImportError(
                "NCT ID must match NCT followed by 8 digits",
                code="invalid_nct_id",
            )
        version = self._get_json(f"{self.api_base}/version")
        study = self._get_json(f"{self.api_base}/studies/{normalized_id}")
        return study, version

    def search(
        self,
        query_parameters: Dict[str, str],
        max_studies: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        """Fetch a deterministic, cursor-paginated public candidate set.

        ``query_parameters`` must not contain the transient ``pageToken``.
        The exact non-cursor parameters are returned for snapshot provenance.
        """
        if "pageToken" in query_parameters:
            raise TrialImportError(
                "pageToken is transient and cannot be part of a selection spec",
                code="invalid_selection",
            )
        if max_studies is not None and max_studies < 1:
            raise TrialImportError(
                "max_studies must be at least 1",
                code="invalid_selection",
            )
        parameters = {str(key): str(value) for key, value in query_parameters.items()}
        parameters.setdefault("format", "json")
        parameters.setdefault("markupFormat", "markdown")
        parameters.setdefault("countTotal", "true")
        parameters.setdefault("pageSize", "100")
        if parameters["format"] != "json":
            raise TrialImportError(
                "Snapshot search requires format=json",
                code="invalid_selection",
            )
        try:
            page_size = int(parameters["pageSize"])
        except ValueError as error:
            raise TrialImportError(
                "pageSize must be an integer",
                code="invalid_selection",
            ) from error
        if not 1 <= page_size <= 1000:
            raise TrialImportError(
                "pageSize must be between 1 and 1000",
                code="invalid_selection",
            )
        if not any(key.startswith("query.") for key in parameters):
            raise TrialImportError(
                "At least one query.* parameter is required",
                code="invalid_selection",
            )

        version = self._get_json(f"{self.api_base}/version")
        studies: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        seen_page_tokens = set()
        page_count = 0
        total_count: Optional[int] = None
        while True:
            request_parameters = dict(parameters)
            if page_token:
                request_parameters["pageToken"] = page_token
            query = urllib.parse.urlencode(request_parameters)
            page = self._get_json(f"{self.api_base}/studies?{query}")
            page_studies = page.get("studies")
            if not isinstance(page_studies, list) or not all(
                isinstance(item, dict) for item in page_studies
            ):
                raise TrialImportError(
                    "ClinicalTrials.gov search response has invalid studies",
                    code="invalid_api_response",
                )
            if total_count is None and isinstance(page.get("totalCount"), int):
                total_count = page["totalCount"]
            page_count += 1
            remaining = (
                None if max_studies is None else max_studies - len(studies)
            )
            if remaining is not None:
                studies.extend(page_studies[:remaining])
            else:
                studies.extend(page_studies)
            if max_studies is not None and len(studies) >= max_studies:
                break
            next_token = page.get("nextPageToken")
            if next_token is None:
                break
            if not isinstance(next_token, str) or not next_token:
                raise TrialImportError(
                    "ClinicalTrials.gov returned an invalid nextPageToken",
                    code="invalid_api_response",
                )
            if next_token in seen_page_tokens:
                raise TrialImportError(
                    "ClinicalTrials.gov repeated a pagination token",
                    code="invalid_api_response",
                )
            seen_page_tokens.add(next_token)
            page_token = next_token
        metadata = {
            "reported_total_count": total_count,
            "pages_fetched": page_count,
            "selection_truncated": (
                total_count is not None and len(studies) < total_count
            ),
        }
        return studies, version, metadata


def load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TrialImportError(
            f"{path} must contain a JSON object",
            code="invalid_json_fixture",
        )
    return payload
