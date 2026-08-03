from dataclasses import dataclass
from typing import Protocol

from providers.models import UsageMetadata


class ResearchProvider(Protocol):
    provider_name: str
    def collect(self, request): ...
    def extract_findings(self, request, sources): ...


class MeetingProvider(Protocol):
    provider_name: str
    def deliberate(self, meeting, report, participants, plan_count): ...


@dataclass(frozen=True)
class IntelligenceProviderResult:
    data: dict
    usage: UsageMetadata | dict | None = None


class FakeResearchProvider:
    provider_name = "fake-research"
    is_paid = False

    def collect(self, request):
        now = request.requested_at
        return IntelligenceProviderResult({"sources": [
            {"source_id":"source-market","source_type":"MARKET","title":"Market fixture","provider":self.provider_name,"published_at":now,"retrieved_at":now,"reference":"fixture:market","structured_summary":"A bounded market observation.","relevance":"HIGH","freshness":"CURRENT","quality":"TEST_FIXTURE","access_status":"AVAILABLE","metadata":{}},
            {"source_id":"source-platform","source_type":"PLATFORM","title":"Platform fixture","provider":self.provider_name,"published_at":now,"retrieved_at":now,"reference":"fixture:platform","structured_summary":"A bounded platform observation with a different view.","relevance":"HIGH","freshness":"CURRENT","quality":"TEST_FIXTURE","access_status":"AVAILABLE","metadata":{"disagreement_group":"position-a"}},
        ]}, UsageMetadata(estimated_cost_usd=0.0))

    def extract_findings(self, request, sources):
        return IntelligenceProviderResult({"findings": [
            {"finding_id":"finding-market","category":"FACT","claim":"The test sources contain two bounded observations.","supporting_source_ids":[value["source_id"] for value in sources],"evidence_summary":"Both normalized fixtures were reviewed.","confidence_level":"MODERATE","limitations":["Fixture evidence only"],"observed_at":request.requested_at,"disagreement":"The platform fixture preserves a distinct position.","metadata":{}},
        ]}, UsageMetadata(estimated_cost_usd=0.0))


class FakeMeetingProvider:
    provider_name = "fake-meeting"
    is_paid = False

    def deliberate(self, meeting, report, participants, plan_count):
        source_ids = list(report.source_ids)
        finding_ids = [value.finding_id for value in report.findings]
        contributions = []
        types = ("ANALYSIS", "PROPOSAL", "RISK", "REVIEW", "REVISION", "RECOMMENDATION")
        for index, participant in enumerate(participants):
            contributions.append({
                "contribution_id": f"contribution-{index + 1}", "meeting_id": meeting.meeting_id,
                "participant_id": participant.employee_id, "role_type": participant.role_type,
                "contribution_type": types[index % len(types)],
                "summary": f"Structured {participant.role_type.lower()} contribution based on reviewed evidence.",
                "evidence_source_ids": source_ids[:1], "referenced_finding_ids": finding_ids[:1],
                "bible_version_metadata": dict(meeting.bible_version_metadata),
                "created_at": meeting.started_at, "metadata": {},
            })
        plans = []
        for index in range(plan_count):
            plans.append({
                "plan_id": f"plan-{index + 1}", "meeting_id": meeting.meeting_id,
                "title": f"Evidence Plan {index + 1}", "concept": f"concept-angle-{index + 1}",
                "target_audience": f"audience-angle-{index + 1}",
                "platform_strategy": f"platform-strategy-{index + 1}",
                "content_direction": f"message-direction-{index + 1}",
                "music_direction": f"music-direction-{index + 1}",
                "image_direction": f"visual-direction-{index + 1}",
                "video_direction": f"video-direction-{index + 1}",
                "marketing_direction": f"marketing-direction-{index + 1}",
                "expected_outputs": ["music", "image", "video", "blog"],
                "supporting_finding_ids": finding_ids[:1],
                "risks": [f"risk-profile-{index + 1}"],
                "differentiation": f"Distinct evidence angle {index + 1}",
                "feasibility": plan_count - index, "estimated_usage": {"estimated_cost_usd":0.0},
                "metadata": {},
            })
        return IntelligenceProviderResult({"contributions":contributions,"plans":plans}, UsageMetadata(estimated_cost_usd=0.0))
