import pytest

from preview_cli.aws import Image
from preview_cli.images import ImageChoice, ImageSource, choose_image

DIGEST = "sha256:" + "b" * 64


class FakeEcr:
    def __init__(self, tags: set[str]) -> None:
        self.tags = tags

    def find(self, repository: str, tag: str) -> Image | None:
        return Image(tag, DIGEST) if tag in self.tags else None


def test_pushed_sha_image_wins_when_it_exists() -> None:
    choice = choose_image("tremor-api", "a1b2c3d4", FakeEcr({"sha-a1b2c3d", "main"}), "old")

    assert choice == ImageChoice(f"sha-a1b2c3d@{DIGEST}", ImageSource.SHA)


def test_falls_back_to_the_environments_current_image() -> None:
    choice = choose_image("tremor-api", "a1b2c3d4", FakeEcr({"main"}), "sha-0000000@sha256:c")

    assert choice == ImageChoice("sha-0000000@sha256:c", ImageSource.CURRENT)


def test_falls_back_to_main_for_a_new_environment() -> None:
    choice = choose_image("tremor-api", "a1b2c3d4", FakeEcr({"main"}), None)

    assert choice == ImageChoice(f"main@{DIGEST}", ImageSource.MAIN)


def test_no_image_at_all_fails_with_the_tags_tried() -> None:
    with pytest.raises(LookupError, match="tremor-api has neither sha-a1b2c3d nor main in ECR"):
        choose_image("tremor-api", "a1b2c3d4", FakeEcr(set()), None)


def test_without_ecr_the_sha_tag_is_used_unpinned() -> None:
    assert choose_image("tremor-api", "a1b2c3d4", None, None) == ImageChoice(
        "sha-a1b2c3d", ImageSource.SHA
    )
