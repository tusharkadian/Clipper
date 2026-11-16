from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0.02, le=1)
    height: float = Field(ge=0.02, le=1)

    @model_validator(mode="after")
    def inside_source(self):
        if self.x + self.width > 1.00000001 or self.y + self.height > 1.00000001:
            raise ValueError("The region must remain inside the source frame.")
        return self


class WebcamLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    webcam: SourceRegion
    gameplay: SourceRegion = Field(default_factory=lambda: SourceRegion(x=0, y=0, width=1, height=1))
    top_fraction: float = Field(default=0.3, ge=0.2, le=0.45)
    webcam_zoom: float = Field(default=1, ge=1, le=2)
    gameplay_fit: Literal["fill", "fit"] = "fit"


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["quiet", "balanced"] = "quiet"
    mode: Literal["clips", "benchmark"] = "clips"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    sample_seconds: int = Field(default=1800, ge=2, le=1800)
    captions: bool = True
    max_clips: int = Field(default=5, ge=1, le=20)
    clip_seconds: int = Field(default=40, ge=15, le=90)
    threshold: float = Field(default=0.55, ge=0.1, le=1)
    zoom: float = Field(default=1.5, ge=1, le=2)
    ai_rerank: bool = True
    encoder: Literal["auto", "cpu", "nvenc"] = "auto"
    analysis_decoder: Literal["auto", "cpu", "nvdec"] = "auto"
    webcam_layout: WebcamLayout | None = None


class URLInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)
    options: RunOptions = Field(default_factory=RunOptions)

    @model_validator(mode="after")
    def validate_source(self) -> URLInput:
        parsed = urlparse(self.url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Use an HTTPS YouTube video or Twitch VOD URL.")
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path != "/watch" or "v=" not in parsed.query:
                raise ValueError("Use a single YouTube watch URL, not a playlist or channel.")
        elif host == "youtu.be":
            if len(parsed.path.strip("/").split("/")) != 1 or not parsed.path.strip("/"):
                raise ValueError("Use a single YouTube video URL.")
        elif host in {"twitch.tv", "www.twitch.tv"}:
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != "videos" or not parts[1].isdigit():
                raise ValueError("Use a completed Twitch VOD URL.")
        else:
            raise ValueError("Only YouTube recordings and Twitch VODs are supported.")
        return self


class Candidate(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    score: float = Field(ge=0, le=1)
    reason: str
    audio_score: float = 0
    visual_score: float = 0
    heuristic_score: float | None = None
    semantic_score: float | None = None
    scene_adjusted: bool = False


class ClipPlan(BaseModel):
    version: int = 10
    source_start: float
    source_end: float
    layout: str = "partial-zoom-blurred-background"
    zoom: float = 1.5
    width: int = 1080
    height: int = 1920
    fps: int = 30
    encoder: Literal["auto", "cpu", "nvenc"] = "auto"
    ranker: str = "activity-v1"
    candidate: Candidate
    webcam_layout: WebcamLayout | None = None