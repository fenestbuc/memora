"""Tests for org graph builder and CEO new-member alerts (Phase 1, Task 2).

Run with: pytest tests/test_org_graph.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from memora.org_graph import build_org_graph, load_members


class TestOrgGraph:
    """TDD for org topology graph and new-member detection."""

    def test_load_members_reads_json_files(self, tmp_path: Path) -> None:
        """load_members should read all *.json files in the given directory."""
        members_dir = tmp_path / "members"
        members_dir.mkdir()

        (members_dir / "ceo-alice.json").write_text(
            json.dumps(
                {"first_name": "Alice", "role": "CEO", "company_github_repo": "r"}
            )
        )
        (members_dir / "gtm-bob.json").write_text(
            json.dumps(
                {"first_name": "Bob", "role": "GTM", "company_github_repo": "r"}
            )
        )

        members = load_members(str(members_dir))
        assert len(members) == 2
        names = {m["first_name"] for m in members}
        assert names == {"Alice", "Bob"}

    def test_load_members_empty_dir(self, tmp_path: Path) -> None:
        """load_members should return an empty list when the dir has no JSON files."""
        members_dir = tmp_path / "members"
        members_dir.mkdir()
        assert load_members(str(members_dir)) == []

    def test_build_org_graph_structure(self) -> None:
        """build_org_graph should return a hierarchical string grouped by role."""
        members = [
            {"first_name": "Alice", "role": "CEO"},
            {"first_name": "Bob", "role": "Engineering"},
            {"first_name": "Charlie", "role": "GTM"},
            {"first_name": "Diana", "role": "Engineering"},
        ]
        graph = build_org_graph(members)

        assert "Memora Digital Twins Network" in graph
        # CEO should appear as a role heading
        assert "CEO" in graph
        # Engineering has two members so expect two lines with engineering names
        assert "Alice" in graph
        assert "Bob" in graph
        assert "Charlie" in graph
        assert "Diana" in graph

    def test_build_org_graph_sorts_alphabetically(self) -> None:
        """Roles and names should be sorted alphabetically in the tree."""
        members = [
            {"first_name": "Zara", "role": "Design"},
            {"first_name": "Aaron", "role": "Engineering"},
            {"first_name": "Mike", "role": "Design"},
        ]
        graph = build_org_graph(members)
        lines = graph.splitlines()

        # Design should appear before Engineering alphabetically
        design_idx = next(i for i, line in enumerate(lines) if line.startswith("Design"))
        eng_idx = next(i for i, line in enumerate(lines) if line.startswith("Engineering"))
        assert design_idx < eng_idx

        # Mike should appear before Zara under Design
        design_block = lines[design_idx:eng_idx]
        mike_idx = next(i for i, line in enumerate(design_block) if "Mike" in line)
        zara_idx = next(i for i, line in enumerate(design_block) if "Zara" in line)
        assert mike_idx < zara_idx

    def test_build_org_graph_empty(self) -> None:
        """build_org_graph should handle an empty member list gracefully."""
        graph = build_org_graph([])
        assert "Memora Digital Twins Network" in graph
        # No members means no roles
        assert "├──" not in graph


class TestCeoNewMemberAlerts:
    """Tests for new-member detection in ceo_digest."""

    def test_get_new_members_detects_first_run(self, tmp_path: Path) -> None:
        """When no state file exists, all members are considered new."""
        members_dir = tmp_path / "members"
        members_dir.mkdir()
        state_path = tmp_path / ".last_digest_members.json"

        (members_dir / "ceo-alice.json").write_text(
            json.dumps(
                {"first_name": "Alice", "role": "CEO", "company_github_repo": "r"}
            )
        )

        from memora.ceo_digest import get_new_members, save_member_state

        new_members = get_new_members(str(members_dir), str(state_path))
        assert len(new_members) == 1
        assert new_members[0]["first_name"] == "Alice"

    def test_get_new_members_ignores_known(self, tmp_path: Path) -> None:
        """Once saved, existing members should not be reported as new again."""
        members_dir = tmp_path / "members"
        members_dir.mkdir()
        state_path = tmp_path / ".last_digest_members.json"

        (members_dir / "ceo-alice.json").write_text(
            json.dumps(
                {"first_name": "Alice", "role": "CEO", "company_github_repo": "r"}
            )
        )

        from memora.ceo_digest import get_new_members, save_member_state

        # First run: mark Alice as known
        new_members = get_new_members(str(members_dir), str(state_path))
        save_member_state(str(members_dir), str(state_path))

        # Second run with the same file
        new_members = get_new_members(str(members_dir), str(state_path))
        assert new_members == []

    def test_get_new_members_detects_addition(self, tmp_path: Path) -> None:
        """Only newly added files should be reported after state update."""
        members_dir = tmp_path / "members"
        members_dir.mkdir()
        state_path = tmp_path / ".last_digest_members.json"

        (members_dir / "ceo-alice.json").write_text(
            json.dumps(
                {"first_name": "Alice", "role": "CEO", "company_github_repo": "r"}
            )
        )

        from memora.ceo_digest import get_new_members, save_member_state

        get_new_members(str(members_dir), str(state_path))
        save_member_state(str(members_dir), str(state_path))

        # New member joins
        (members_dir / "gtm-bob.json").write_text(
            json.dumps(
                {"first_name": "Bob", "role": "GTM", "company_github_repo": "r"}
            )
        )

        new_members = get_new_members(str(members_dir), str(state_path))
        assert len(new_members) == 1
        assert new_members[0]["first_name"] == "Bob"

    def test_send_new_member_alert_logs(self, tmp_path: Path) -> None:
        """send_new_member_alert should log a summary when new members exist."""
        members_dir = tmp_path / "members"
        members_dir.mkdir()
        state_path = tmp_path / ".last_digest_members.json"

        (members_dir / "ceo-alice.json").write_text(
            json.dumps(
                {"first_name": "Alice", "role": "CEO", "company_github_repo": "r"}
            )
        )

        from memora.ceo_digest import send_new_member_alert

        with patch("memora.ceo_digest.logger") as mock_logger:
            send_new_member_alert(str(members_dir), str(state_path))

        info_calls = [call for call in mock_logger.info.call_args_list]
        assert any("Alice" in str(call) for call in info_calls)
