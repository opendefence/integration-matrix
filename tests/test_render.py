import yaml

from opendefence_integration_matrix.render import DIRECTORY, render


def test_committed_manifests_are_the_rendered_ones() -> None:
    for name, content in render().items():
        assert (DIRECTORY / name).read_text() == content


def test_cluster_access_is_reading_users() -> None:
    roles = [document for document in yaml.safe_load_all(render()["rbac.yaml"]) if document["kind"] == "ClusterRole"]
    assert [role["rules"] for role in roles] == [
        [{"apiGroups": ["platform.opendefence.fi"], "resources": ["users"], "verbs": ["get", "list", "watch"]}]
    ]
