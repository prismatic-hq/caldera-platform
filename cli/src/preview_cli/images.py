from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from preview_cli.aws import Image
from preview_cli.commands import image_tag

MAIN_TAG = "main"


class ImageSource(StrEnum):
    SHA = "sha"
    CURRENT = "current"
    MAIN = "main"


@dataclass(frozen=True)
class ImageChoice:
    tag: str
    source: ImageSource


class ImageFinder(Protocol):
    def find(self, repository: str, tag: str) -> Image | None: ...


def choose_image(
    repository: str, sha: str, ecr: ImageFinder | None, current_tag: str | None
) -> ImageChoice:
    """Optimistic start: the commit's image, else the environment's current image, else main."""
    tag = image_tag(sha)
    if ecr is None:
        return ImageChoice(tag, ImageSource.SHA)
    if pushed := ecr.find(repository, tag):
        return ImageChoice(pushed.reference, ImageSource.SHA)
    if current_tag:
        return ImageChoice(current_tag, ImageSource.CURRENT)
    if main := ecr.find(repository, MAIN_TAG):
        return ImageChoice(main.reference, ImageSource.MAIN)
    raise LookupError(f"{repository} has neither {tag} nor {MAIN_TAG} in ECR")
