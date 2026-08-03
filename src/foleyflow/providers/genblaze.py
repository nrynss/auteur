"""Auteur's adapters, lifted into Genblaze ``SyncProvider``s.

``providers/base.py`` says the ``Adapter`` protocol is "deliberately shaped like
Genblaze's ``SyncProvider.generate(step) -> Step`` so the adapter can be lifted
into a connector with thin translation rather than a rewrite". This module is
that lift, and nothing more: the vendor code still lives in ``fal.py``,
``local.py``, ``google.py`` and ``openai.py``, and this file only translates
between a ``Step`` and an ``Adapter``.

Three SFX tiers reach Genblaze three different ways:

* **paid** — ElevenLabs sound-effects v2 through the shipped
  ``genblaze-elevenlabs`` connector. No lift needed; the connector *is* a
  ``SyncProvider``. Resolved lazily so importing Auteur never imports the
  ``elevenlabs`` SDK and never looks for ``ELEVENLABS_API_KEY``.
* **fallback** — ``fal-ai/elevenlabs/sound-effects/v2`` through
  :class:`FalSfxProvider`, the lift of ``providers/fal.py``. Genblaze ships no
  fal connector (checked at 0.3.7), so the lift is how fal reaches the
  orchestrator at all.
* **test** — the self-hosted MOSS worker through :class:`LocalSfxProvider`.
  Putting the offline tier behind Genblaze too is the point: the tier becomes a
  *config* difference rather than a *code* difference, so the paid path's
  orchestration is exercised by every offline test run instead of for the first
  time during a live demo.

Analysis (the VLM timeline step) is the same lift for the Google and OpenAI
adapters: :class:`AnalysisProvider` runs the call through
:func:`foleyflow.ledger.execute_with_ledger` and parks the text response on
``step.provider_payload``. ``genblaze-google`` / ``genblaze-openai`` on PyPI are
media-generation connectors (Veo, Sora, …); chat/vision analysis stays behind
Auteur's own adapters so the TIMELINE_PROMPT path and cost ledger do not
fork.

## Why every SFX provider materializes bytes locally

``genblaze_core.validate_asset_url`` allows HTTPS only, and the self-hosted
worker answers on plain HTTP at a loopback address. Rather than skip the guard,
each SFX provider downloads its own result and attaches a ``file://`` asset
under :func:`sfx_output_dir` — which is below the system temp dir, the only
place Genblaze will read a ``file://`` input back from. That also makes all
three tiers return the same shape, which is what lets the caller treat them
interchangeably.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from genblaze_core import (
    Asset,
    Modality,
    ProviderCapabilities,
    Step,
    SyncProvider,
)
from genblaze_core._utils import local_file_url
from genblaze_core.exceptions import ProviderError
from genblaze_core.models.enums import ProviderErrorCode

from foleyflow.providers.base import ModelSpec

__all__ = [
    "AnalysisProvider",
    "ElevenLabsSfxUnavailable",
    "FFmpegStageProvider",
    "FalSfxProvider",
    "FoleyAdapterProvider",
    "GoogleAnalysisProvider",
    "LedgerRecordingMixin",
    "LocalSfxProvider",
    "OpenAIAnalysisProvider",
    "asset_local_path",
    "get_provider",
    "run_analysis_step",
    "sfx_output_dir",
]


class ElevenLabsSfxUnavailable(RuntimeError):
    """The ``genblaze-elevenlabs`` connector is not importable."""


def sfx_output_dir() -> str:
    """Where providers write raw generated audio.

    Under ``workflow.work_dir()`` rather than a bare ``tempfile.mkdtemp``: that
    directory is already the one Genblaze is willing to read ``file://`` inputs
    from, so a stem produced here can be fed straight back into a later stage.
    """
    from foleyflow.workflow import work_dir

    path = os.path.join(work_dir(), "sfx")
    os.makedirs(path, exist_ok=True)
    return path


def asset_local_path(asset: Asset) -> str:
    """The local path behind an ``Asset`` produced by any of our providers.

    Every tier lands its bytes on disk (see the module docstring), so this is
    total for our own assets and raises for anything else rather than silently
    handing back a URL the caller would then try to open as a path.
    """
    parsed = urlparse(asset.url)
    if parsed.scheme != "file":
        raise ValueError(f"expected a file:// asset, got {asset.url!r}")
    return url2pathname(parsed.path)


def _materialize(url: str, dest_path: str,
                 headers: Optional[Dict[str, str]] = None) -> str:
    """Copy or download ``url`` to ``dest_path``. Returns ``dest_path``."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        shutil.copyfile(url2pathname(parsed.path), dest_path)
        return dest_path
    if parsed.scheme in ("http", "https"):
        from foleyflow import net

        # The MOSS worker serialises on one GPU, so a queued request can wait
        # behind another; net.download's 60s default would fail the second
        # concurrent stem. Matches providers.local.GENERATE_TIMEOUT_S.
        #
        # `headers` is passed only when there are some, so a stubbed
        # net.download with the historical three-argument signature still works.
        if headers:
            return net.download(url, dest_path, timeout=300.0, headers=headers)
        return net.download(url, dest_path, timeout=300.0)
    raise ValueError(f"cannot materialize asset URL with scheme {parsed.scheme!r}")


class FoleyAdapterProvider(SyncProvider):
    """Base lift: one Auteur ``Adapter`` behind Genblaze's provider contract.

    ``generate`` goes through :func:`foleyflow.ledger.execute_with_ledger`
    rather than calling the adapter directly, so moving generation under
    Genblaze does not move it out of the cost ledger. The ledger row is written
    for failures too, exactly as before.
    """

    adapter_name: str = ""
    media_type: str = "audio/wav"

    def __init__(self, *, adapter: Any = None, output_dir: Optional[str] = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter = adapter
        self._output_dir = output_dir

    @property
    def adapter(self) -> Any:
        if self._adapter is None:
            from foleyflow.providers import get_adapter

            self._adapter = get_adapter(self.adapter_name)
        return self._adapter

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.AUDIO],
            supported_inputs=["text"],
            max_duration=30.0,
            output_formats=["audio/wav"],
        )

    def _spec(self, model: str) -> ModelSpec:
        from foleyflow.providers.config import get_model_spec

        try:
            return get_model_spec(model)
        except KeyError:
            # Mirrors ledger.execute_with_ledger's tolerance of undeclared
            # endpoints: an unknown model is a config gap, not a crash.
            return ModelSpec(model_id=model, adapter=self.adapter_name, role="sfx")

    def generate(self, step: Step, config: Any = None) -> Step:
        from foleyflow.ledger import execute_with_ledger
        from foleyflow.providers.fal import extract_audio_url

        arguments = dict(step.params)
        try:
            raw = execute_with_ledger(step.model, arguments=arguments,
                                      adapter=self.adapter)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"{self.name}: {step.model} generation failed: {exc}",
                error_code=ProviderErrorCode.SERVER_ERROR,
            ) from exc

        url = extract_audio_url(raw)
        if not url:
            raise ProviderError(
                f"{self.name}: {step.model} returned no audio URL",
                error_code=ProviderErrorCode.MODEL_ERROR,
            )

        out_dir = self._output_dir or sfx_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, f"{step.step_id}.audio")
        # Fetching the result is a second HTTP request, and for the self-hosted
        # worker it is as protected as /generate. The adapter decides — it is
        # the only thing that knows which URLs are its own, and asking it here
        # keeps this loop from ever attaching a credential to a vendor CDN.
        headers_fn = getattr(self.adapter, "download_headers", None)
        headers = headers_fn(url) if callable(headers_fn) else None
        _materialize(url, dest, headers)

        asset = Asset(url=local_file_url(Path(dest).resolve()),
                      media_type=self.media_type)
        asset.metadata["audio_type"] = "sfx"
        asset.metadata["source_url"] = url
        step.assets.append(asset)
        return step


class LocalSfxProvider(FoleyAdapterProvider):
    """The self-hosted MOSS-SoundEffect worker (SPEC §9.1) as a provider."""

    name = "foleyflow-local-sfx"
    adapter_name = "local"


class FalSfxProvider(FoleyAdapterProvider):
    """fal.ai as a provider. Genblaze ships no fal connector; this is the lift."""

    name = "foleyflow-fal-sfx"
    adapter_name = "fal"
    media_type = "audio/mpeg"


class LedgerRecordingMixin:
    """Puts a vendor-shipped connector's calls into the cost ledger.

    :class:`FoleyAdapterProvider` reaches the ledger by calling
    ``execute_with_ledger`` itself, because it owns the vendor call. A connector
    Genblaze ships — ``genblaze-elevenlabs``, the paid SFX tier — owns its own
    HTTP, so there is no seam inside it to hook. Without this mixin the most
    expensive tier in the table is the only one that bills silently: no ledger
    row, no project attribution, no budget alert.

    Mixed in ahead of the connector class by :func:`get_provider`, so
    ``super().generate`` is the connector's real implementation and every other
    part of its contract (capabilities, discovery, retry policy) is untouched.

    Cost is the declared estimate: the ElevenLabs SFX API answers with audio
    bytes and no billing metadata, so ``cost_source`` stays ``"estimate"`` here
    rather than claiming a precision the response does not carry.
    """

    def generate(self, step: Step, config: Any = None) -> Step:
        from foleyflow.ledger import (
            estimate_cost,
            get_params_hash,
            log_transaction,
        )

        arguments = dict(step.params)
        params_hash = get_params_hash(arguments)
        cost = estimate_cost(step.model, arguments)
        try:
            result = super().generate(step, config)  # type: ignore[misc]
        except Exception as exc:
            # Failures are billed too — a rejected call can still have cost the
            # account something, and a silent failure is worse than an
            # over-count. Mirrors execute_with_ledger's except branch.
            log_transaction(step.model, params_hash, cost, success=False,
                            error=str(exc))
            raise
        log_transaction(step.model, params_hash, cost, success=True,
                        cost_source="estimate")
        return result


class AnalysisProvider(SyncProvider):
    """Multimodal VLM call (timeline analysis), lifted into Genblaze.

    Sibling of :class:`FoleyAdapterProvider`: same ledger seam, same adapter
    resolution — but the result is text JSON in ``step.provider_payload``, not
    an audio asset. One step per analysis so Genblaze times, retries and
    records the call the same way it does a stem.
    """

    adapter_name: str = ""
    name: str = "foleyflow-analysis"

    def __init__(self, *, adapter: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter = adapter

    @property
    def adapter(self) -> Any:
        if self._adapter is None:
            from foleyflow.providers import get_adapter

            self._adapter = get_adapter(self.adapter_name)
        return self._adapter

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.TEXT, Modality.VIDEO, Modality.IMAGE],
            supported_inputs=["text", "video", "image"],
            output_formats=["text/plain", "application/json"],
        )

    def generate(self, step: Step, config: Any = None) -> Step:
        from foleyflow.ledger import execute_with_ledger

        arguments = dict(step.params)
        try:
            raw = execute_with_ledger(
                step.model, arguments=arguments, adapter=self.adapter)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"{self.name}: {step.model} analysis failed: {exc}",
                error_code=ProviderErrorCode.SERVER_ERROR,
            ) from exc

        if not isinstance(raw, dict):
            raw = {"output": "" if raw is None else str(raw)}
        step.provider_payload = raw
        return step


class GoogleAnalysisProvider(AnalysisProvider):
    """Gemini (and other Google VLMs) for Foley timeline analysis."""

    name = "foleyflow-google-analysis"
    adapter_name = "google"


class OpenAIAnalysisProvider(AnalysisProvider):
    """OpenAI vision models for Foley timeline analysis."""

    name = "foleyflow-openai-analysis"
    adapter_name = "openai"


def run_analysis_step(endpoint: str, arguments: dict, *,
                      run_name: str = "foleyflow-analyze") -> dict:
    """One analysis call as a single-step Genblaze pipeline.

    Returns the provider's raw response dict (``{"output": ...}``), matching
    what :func:`foleyflow.ledger.execute_with_ledger` used to hand back so
    callers keep parsing the same envelope. Timing, status and provenance
    travel on the Genblaze run; the cost ledger still gets a row because the
    provider goes through ``execute_with_ledger``.

    Always builds an :class:`AnalysisProvider` for the endpoint's Auteur
    adapter — never the SFX ``genblaze_provider`` (fal/local materialize audio
    and would mis-handle a text response). Google and OpenAI declare their
    analysis lifts in providers.toml for discovery; this function is the call
    site that actually uses them.
    """
    from genblaze_core import Modality, Pipeline, StepType

    from foleyflow.providers import get_adapter
    from foleyflow.providers.config import get_model_spec

    adapter_name = get_model_spec(endpoint).adapter
    # Prefer the declared analysis class when it is an AnalysisProvider so the
    # provider name in Genblaze manifests matches providers.toml; otherwise
    # wrap the plain adapter (retired fal analysis routes still work).
    try:
        declared = get_provider(adapter_name)
    except KeyError:
        declared = None
    if isinstance(declared, AnalysisProvider):
        provider = declared
    else:
        provider = AnalysisProvider(adapter=get_adapter(adapter_name))
        provider.adapter_name = adapter_name
        provider.name = f"foleyflow-{adapter_name}-analysis"

    pipeline = Pipeline(run_name, chain=False, preflight=False)
    pipeline.step(
        provider,
        model=endpoint,
        prompt=arguments.get("prompt") or "",
        modality=Modality.TEXT,
        step_type=StepType.GENERATE,
        params=dict(arguments),
    )
    # raise_on_failure=True: a failed step raises ProviderError out of the
    # pipeline, so there is no "empty succeeded_steps" branch to handle —
    # the exception is the failure path.
    try:
        import asyncio
        asyncio.get_running_loop()
    except RuntimeError:
        import asyncio as _asyncio
        outcome = _asyncio.run(
            pipeline.arun(fail_fast=True, raise_on_failure=True, progress=False))
    else:
        outcome = pipeline.run(
            fail_fast=True, raise_on_failure=True, progress=False)

    steps = outcome.succeeded_steps()
    payload = steps[0].provider_payload or {}
    if not isinstance(payload, dict):
        return {"output": str(payload)}
    return payload


class FFmpegStageProvider(SyncProvider):
    """Auteur's deterministic local stages, as one Genblaze provider.

    A sibling of ``genblaze_core.FFmpegTransform``: same ``SyncProvider``
    contract, same "shell out to local ffmpeg" posture, but the operations are
    Auteur's own (assemble the mix, mux the video, measure QA) rather than
    the generic resize/crop set. Registering them here is what makes mix, mux
    and QA stages Genblaze times, retries and records — instead of subprocess
    calls that happen invisibly beside it.

    ``step.model`` names the registered stage. The stage callable receives the
    step's params and returns a dict; an ``"output_path"`` key becomes the
    step's output asset, and the whole dict is retrievable via
    :meth:`result_for` (kept off the ``Step`` so nothing unhashable reaches the
    provenance manifest).
    """

    name = "foleyflow-ffmpeg"

    def __init__(self, stages: Optional[Dict[str, Callable[[dict], dict]]] = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stages: Dict[str, Callable[[dict], dict]] = dict(stages or {})
        self._results: Dict[str, dict] = {}

    def register(self, stage: str, fn: Callable[[dict], dict]) -> None:
        self._stages[stage] = fn

    def result_for(self, stage: str) -> Optional[dict]:
        return self._results.get(stage)

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.AUDIO, Modality.VIDEO],
            supported_inputs=["audio", "video"],
            accepts_chain_input=True,
            output_formats=["audio/wav", "video/mp4"],
        )

    def generate(self, step: Step, config: Any = None) -> Step:
        fn = self._stages.get(step.model)
        if fn is None:
            raise ProviderError(
                f"{self.name}: no stage registered as {step.model!r} "
                f"(known: {sorted(self._stages)})",
                error_code=ProviderErrorCode.INVALID_INPUT,
            )
        try:
            out = fn(dict(step.params)) or {}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"{self.name}: stage {step.model!r} failed: {exc}",
                error_code=ProviderErrorCode.SERVER_ERROR,
            ) from exc

        self._results[step.model] = out
        path = out.get("output_path")
        if path and os.path.exists(path):
            step.assets.append(Asset(
                url=local_file_url(Path(path).resolve()),
                media_type=out.get("media_type") or "application/octet-stream",
            ))
        return step


# -- resolution ---------------------------------------------------------------

_PROVIDER_CACHE: Dict[str, Any] = {}


def get_provider(adapter_name: str, *, refresh: bool = False) -> Any:
    """The Genblaze provider for a Auteur adapter name, from providers.toml.

    Declared as ``[adapters.<name>] genblaze_provider = "module:Class"`` so the
    tier table stays data. Instances are cached per adapter name: Genblaze
    providers hold a preflight latch and a model registry, and rebuilding one
    per stem would redo that work on every call.
    """
    if not refresh and adapter_name in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[adapter_name]

    import importlib
    import inspect

    from foleyflow.providers.config import get_adapter_config

    cfg = get_adapter_config(adapter_name)
    target = cfg.get("genblaze_provider")
    if not target or ":" not in target:
        raise KeyError(
            f"Adapter {adapter_name!r} declares no valid 'genblaze_provider' in "
            "providers.toml (expected 'package.module:ClassName')")

    module_path, class_name = target.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        if module_path.startswith("genblaze_elevenlabs"):
            raise ElevenLabsSfxUnavailable(
                "genblaze-elevenlabs is not installed; the paid SFX tier is "
                "unavailable. Install it or select another tier "
                "(FOLEYFLOW_SFX_TIER).") from exc
        raise KeyError(
            f"Adapter {adapter_name!r}: {module_path!r} is not importable: {exc}"
        ) from exc

    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise KeyError(
            f"Adapter {adapter_name!r}: {module_path!r} has no {class_name!r}"
        ) from exc

    # Connectors differ in what they accept; pass only what they declare. The
    # API key is deliberately NOT passed — every connector we use reads its own
    # environment variable, so no key is ever held in Auteur's memory.
    kwargs: Dict[str, Any] = {}
    params = inspect.signature(cls).parameters
    if "output_dir" in params:
        kwargs["output_dir"] = sfx_output_dir()

    # Our own lifts already write their ledger row inside `generate`; a
    # vendor-shipped connector has no such seam, so one is mixed in here. Doing
    # it at the single construction site means a new connector in
    # providers.toml is ledgered by existence, not by remembering to wrap it.
    if not issubclass(cls, (FoleyAdapterProvider, AnalysisProvider)):
        cls = type(f"Ledgered{cls.__name__}", (LedgerRecordingMixin, cls), {})

    provider = cls(**kwargs)
    _PROVIDER_CACHE[adapter_name] = provider
    return provider
