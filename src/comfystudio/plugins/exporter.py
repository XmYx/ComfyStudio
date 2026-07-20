#!/usr/bin/env python
"""
Project exporter plugin.

Exports the rendered shots in whatever media form the user needs, adapting to
each shot's **actual last output** (image or video):

  * Video            - transcode each shot's video to a container/codec, or
                       merge them into one clip. Image shots become a clip of
                       the shot's duration.
  * Still - single   - one image per shot: a chosen frame extracted from video
                       shots, or the still itself for image shots.
  * Image sequence   - every frame of video shots as numbered images (image
                       shots contribute their single frame).
  * Audio only       - the audio track of video shots (or audio-output shots),
                       transcoded to a chosen format; optionally merged.

Formats and codecs are selectable per media type. Sources that can't satisfy
the chosen type (e.g. an image when exporting audio) are skipped and reported.
"""

import os
import shutil
import subprocess
import copy

from qtpy.QtWidgets import (
    QAction, QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox, QHBoxLayout,
    QVBoxLayout, QDialogButtonBox, QLabel, QFileDialog, QMessageBox, QPushButton,
    QSpinBox, QDoubleSpinBox, QGroupBox
)
from qtpy.QtCore import Qt


# --- Export type identifiers -------------------------------------------------
EXPORT_VIDEO = "video"
EXPORT_FRAME = "frame"
EXPORT_SEQUENCE = "sequence"
EXPORT_AUDIO = "audio"

EXPORT_TYPE_LABELS = [
    ("Video", EXPORT_VIDEO),
    ("Still image - single frame", EXPORT_FRAME),
    ("Image sequence", EXPORT_SEQUENCE),
    ("Audio only", EXPORT_AUDIO),
]

# Audio format -> (ffmpeg codec, file extension).
AUDIO_FORMATS = {
    "wav": ("pcm_s16le", ".wav"),
    "mp3": ("libmp3lame", ".mp3"),
    "flac": ("flac", ".flac"),
    "aac (m4a)": ("aac", ".m4a"),
    "ogg (vorbis)": ("libvorbis", ".ogg"),
    "opus": ("libopus", ".opus"),
}

VIDEO_CONTAINERS = ["mp4", "mov", "mkv", "webm"]
VIDEO_CODECS = ["libx264", "libx265", "prores_ks", "vp9", "mpeg4", "copy"]
IMAGE_FORMATS = ["png", "jpg", "webp", "tiff", "bmp"]


def register(app):
    exporterAction = QAction("Export Project", app)
    file_menu = None
    for action in app.menuBar().actions():
        if action.text() == "File":
            file_menu = action.menu()
            break
    if file_menu:
        file_menu.addAction(exporterAction)
    exporterAction.triggered.connect(lambda: openExportDialog(app))


def openExportDialog(app):
    if not shutil.which("ffmpeg"):
        QMessageBox.critical(app, "Export", "ffmpeg was not found on PATH. Install ffmpeg to export.")
        return
    dialog = ExportShotsDialog(app)
    if dialog.exec() == QDialog.Accepted:
        config = dialog.getConfig()
        app.settingsManager.set("export_config", config)
        app.settingsManager.save()
        shots = selectedExportShots(app, config.get("scope", "all"))
        performExport(app, config, copy.deepcopy(shots))


# --- Shot / media helpers ----------------------------------------------------
def selectedExportShots(app, scope):
    """The shots to export: all, or the ones selected in the shot list."""
    if scope == "selected" and hasattr(app, "listWidget"):
        indices = []
        for item in app.listWidget.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(idx, int) and 0 <= idx < len(app.shots):
                indices.append(idx)
        if indices:
            return [app.shots[i] for i in sorted(set(indices))]
    return list(app.shots)


def shot_final_output(shot):
    """
    (path, media_type) of a shot's most recent rendered output. Prefers the
    newest enabled-workflow output (by version timestamp / file mtime), so it
    reflects "the last output of the shot" whether that is an image, video, or
    audio. Falls back to the shot-level video/still paths. Returns (None, None)
    when nothing has been rendered.
    """
    best = None  # (timestamp, path, media_type)
    for wf in getattr(shot, "workflows", []) or []:
        if not getattr(wf, "enabled", True):
            continue
        path = getattr(wf, "lastOutput", "") or ""
        if not path or not os.path.exists(path):
            continue
        mtype = (getattr(wf, "lastOutputType", "") or "").upper()
        if not mtype:
            mtype = "VIDEO" if getattr(wf, "lastOutputIsVideo", False) else "IMAGE"
        ts = 0.0
        versions = getattr(wf, "versions", []) or []
        if versions:
            ts = max((v.get("timestamp", 0) or 0) for v in versions)
        if not ts:
            try:
                ts = os.path.getmtime(path)
            except OSError:
                ts = 0.0
        if best is None or ts >= best[0]:
            best = (ts, path, mtype)
    if best:
        return best[1], best[2]

    # Fallback to shot-level paths; newer mtime wins.
    candidates = []
    for attr, mtype in (("videoPath", "VIDEO"), ("stillPath", "IMAGE")):
        p = getattr(shot, attr, "") or ""
        if p and os.path.exists(p):
            try:
                candidates.append((os.path.getmtime(p), p, mtype))
            except OSError:
                candidates.append((0.0, p, mtype))
    if candidates:
        candidates.sort()
        return candidates[-1][1], candidates[-1][2]
    return None, None


def shot_duration(shot):
    try:
        d = float(getattr(shot, "duration", 0) or 0)
        return d if d > 0 else 5.0
    except Exception:
        return 5.0


def safe_name(shot, index):
    base = (getattr(shot, "name", "") or "shot").strip().replace(" ", "_")
    base = "".join(c for c in base if c.isalnum() or c in ("_", "-")) or "shot"
    return f"{index + 1:03d}_{base}"


def _probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _has_audio_stream(path):
    if not shutil.which("ffprobe"):
        return True  # can't tell; let ffmpeg try
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True)
        return bool(out.stdout.strip())
    except Exception:
        return True


# --- Export dispatch ---------------------------------------------------------
def performExport(app, config, shots):
    etype = config["type"]
    outputs = []
    for i, shot in enumerate(shots):
        src, mtype = shot_final_output(shot)
        outputs.append((i, shot, src, mtype))

    rendered = [o for o in outputs if o[2]]
    if not rendered:
        QMessageBox.information(app, "Export", "No rendered shot outputs found to export.")
        return

    if etype == EXPORT_VIDEO:
        exported, skipped = export_video(app, config, rendered)
    elif etype == EXPORT_FRAME:
        exported, skipped = export_single_frame(app, config, rendered)
    elif etype == EXPORT_SEQUENCE:
        exported, skipped = export_sequence(app, config, rendered)
    elif etype == EXPORT_AUDIO:
        exported, skipped = export_audio(app, config, rendered)
    else:
        QMessageBox.warning(app, "Export", f"Unknown export type: {etype}")
        return

    msg = f"Export completed.\n\nExported: {exported}"
    if skipped:
        msg += f"\nSkipped (incompatible source): {skipped}"
    QMessageBox.information(app, "Export", msg)


def run_ffmpeg(cmd, app):
    """Run ffmpeg; returns True on success, shows the error tail on failure."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        QMessageBox.critical(app, "FFmpeg Error", str(e))
        return False
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        QMessageBox.critical(app, "FFmpeg Error",
                             f"ffmpeg failed:\n\n{tail}\n\nCommand:\n{' '.join(cmd)}")
        return False
    return True


def _video_adv_options(advanced, codec):
    opts = []
    if codec != "copy":
        if advanced.get("crf") is not None:
            opts.extend(["-crf", str(advanced["crf"])])
        if advanced.get("video_bitrate"):
            opts.extend(["-b:v", f"{advanced['video_bitrate']}k"])
        if advanced.get("preset") and codec in ("libx264", "libx265"):
            opts.extend(["-preset", advanced["preset"]])
        # h.264/h.265 need an even, browser-friendly pixel format.
        if codec in ("libx264", "libx265"):
            opts.extend(["-pix_fmt", "yuv420p"])
    if advanced.get("frame_rate"):
        opts.extend(["-r", str(advanced["frame_rate"])])
    return opts


def export_video(app, config, rendered):
    dest = config["dest"]
    codec = config["video_codec"]
    container = config["container"]
    advanced = config.get("advanced", {})
    acodec = advanced.get("audio_codec", "aac")
    abitrate = advanced.get("audio_bitrate")
    merge = config["merge"]
    adv = _video_adv_options(advanced, codec)

    def audio_opts():
        o = ["-c:a", acodec]
        if abitrate:
            o.extend(["-b:a", f"{abitrate}k"])
        return o

    exported = skipped = 0

    # Build normalized per-shot clips first (needed for a reliable merge).
    if not dest:
        QMessageBox.warning(app, "Export", "No destination selected.")
        return 0, 0

    if merge:
        out_dir = os.path.dirname(dest) or "."
        tmp_dir = os.path.join(out_dir, ".export_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        clips = []
        for i, shot, src, mtype in rendered:
            clip = os.path.join(tmp_dir, f"{safe_name(shot, i)}.{container}")
            if _make_video_clip(app, src, mtype, shot, clip, codec, adv, audio_opts()):
                clips.append(clip)
                exported += 1
            else:
                skipped += 1
        if clips:
            list_path = os.path.join(tmp_dir, "merge_list.txt")
            with open(list_path, "w") as f:
                for c in clips:
                    f.write(f"file '{os.path.abspath(c)}'\n")
            ok = run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                             "-i", list_path, "-c", "copy", dest], app)
            if not ok:
                # Fall back to re-encoding the concatenation.
                run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                            "-c:v", codec] + adv + audio_opts() + [dest], app)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return exported, skipped

    # Individual clips into a folder.
    if not os.path.isdir(dest):
        QMessageBox.warning(app, "Export", "Please select a valid destination folder.")
        return 0, 0
    for i, shot, src, mtype in rendered:
        out_full = os.path.join(dest, f"{safe_name(shot, i)}.{container}")
        if _make_video_clip(app, src, mtype, shot, out_full, codec, adv, audio_opts()):
            exported += 1
        else:
            skipped += 1
    return exported, skipped


def _make_video_clip(app, src, mtype, shot, out_path, codec, adv, audio_opts):
    """Produce a single video clip from a video or image source."""
    if mtype == "VIDEO":
        cmd = ["ffmpeg", "-y", "-i", src, "-c:v", codec] + adv + audio_opts + [out_path]
        return run_ffmpeg(cmd, app)
    if mtype == "IMAGE":
        # A still becomes a clip lasting the shot's duration.
        vcodec = codec if codec != "copy" else "libx264"
        dur = shot_duration(shot)
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", src, "-t", f"{dur:.3f}",
               "-c:v", vcodec] + [a for a in adv if a not in ("-pix_fmt", "yuv420p")]
        cmd += ["-pix_fmt", "yuv420p", out_path]
        return run_ffmpeg(cmd, app)
    return False  # audio source: nothing to make a video from


def _image_quality_opts(fmt, quality):
    if fmt == "jpg":
        # ffmpeg mjpeg q:v is 2 (best) .. 31 (worst).
        q = max(2, min(31, round(31 - (quality / 100.0) * 29)))
        return ["-q:v", str(q)]
    if fmt == "webp":
        return ["-quality", str(quality)]
    return []


def export_single_frame(app, config, rendered):
    dest = config["dest"]
    if not dest or not os.path.isdir(dest):
        QMessageBox.warning(app, "Export", "Please select a valid destination folder.")
        return 0, 0
    fmt = config["image_format"]
    ext = "jpg" if fmt == "jpg" else fmt
    quality = config.get("image_quality", 90)
    position = config.get("frame_position", "first")
    qopts = _image_quality_opts(fmt, quality)

    exported = skipped = 0
    for i, shot, src, mtype in rendered:
        out_full = os.path.join(dest, f"{safe_name(shot, i)}.{ext}")
        if mtype == "IMAGE":
            ok = run_ffmpeg(["ffmpeg", "-y", "-i", src] + qopts + [out_full], app)
        elif mtype == "VIDEO":
            pre = []
            if position == "first":
                pass
            elif position == "last":
                pre = ["-sseof", "-1"]
            else:  # middle
                dur = _probe_duration(src)
                if dur > 0:
                    pre = ["-ss", f"{dur / 2.0:.3f}"]
            cmd = ["ffmpeg", "-y"] + pre + ["-i", src, "-frames:v", "1", "-update", "1"] + qopts + [out_full]
            ok = run_ffmpeg(cmd, app)
        else:
            skipped += 1
            continue
        exported += 1 if ok else 0
        skipped += 0 if ok else 1
    return exported, skipped


def export_sequence(app, config, rendered):
    dest = config["dest"]
    if not dest or not os.path.isdir(dest):
        QMessageBox.warning(app, "Export", "Please select a valid destination folder.")
        return 0, 0
    fmt = config["image_format"]
    ext = "jpg" if fmt == "jpg" else fmt
    quality = config.get("image_quality", 90)
    fps = config.get("sequence_fps") or 0
    merge = config["merge"]  # single folder with per-shot prefixes vs subfolders
    qopts = _image_quality_opts(fmt, quality)

    exported = skipped = 0
    for i, shot, src, mtype in rendered:
        name = safe_name(shot, i)
        if merge:
            pattern = os.path.join(dest, f"{name}_%05d.{ext}")
        else:
            subdir = os.path.join(dest, name)
            os.makedirs(subdir, exist_ok=True)
            pattern = os.path.join(subdir, f"frame_%05d.{ext}")

        if mtype == "VIDEO":
            rate = ["-r", str(fps)] if fps else []
            cmd = ["ffmpeg", "-y", "-i", src] + rate + qopts + [pattern]
            ok = run_ffmpeg(cmd, app)
        elif mtype == "IMAGE":
            single = pattern % 1
            ok = run_ffmpeg(["ffmpeg", "-y", "-i", src] + qopts + [single], app)
        else:
            skipped += 1
            continue
        exported += 1 if ok else 0
        skipped += 0 if ok else 1
    return exported, skipped


def export_audio(app, config, rendered):
    fmt_label = config["audio_format"]
    acodec, ext = AUDIO_FORMATS.get(fmt_label, ("pcm_s16le", ".wav"))
    abitrate = config.get("audio_bitrate")
    merge = config["merge"]
    dest = config["dest"]

    def acodec_opts():
        o = ["-vn", "-c:a", acodec]
        if abitrate and acodec not in ("pcm_s16le", "flac"):
            o.extend(["-b:a", f"{abitrate}k"])
        return o

    # Collect audio-bearing sources.
    usable = []
    skipped = 0
    for i, shot, src, mtype in rendered:
        if mtype == "AUDIO" or (mtype == "VIDEO" and _has_audio_stream(src)):
            usable.append((i, shot, src))
        else:
            skipped += 1
    if not usable:
        QMessageBox.information(app, "Export", "No shots with audio to export.")
        return 0, skipped

    exported = 0
    if merge:
        if not dest:
            QMessageBox.warning(app, "Export", "No destination file selected.")
            return 0, skipped
        out_dir = os.path.dirname(dest) or "."
        tmp_dir = os.path.join(out_dir, ".export_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        parts = []
        for i, shot, src in usable:
            part = os.path.join(tmp_dir, f"{safe_name(shot, i)}{ext}")
            if run_ffmpeg(["ffmpeg", "-y", "-i", src] + acodec_opts() + [part], app):
                parts.append(part)
                exported += 1
        if parts:
            list_path = os.path.join(tmp_dir, "audio_list.txt")
            with open(list_path, "w") as f:
                for p in parts:
                    f.write(f"file '{os.path.abspath(p)}'\n")
            if not run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                               "-i", list_path, "-c", "copy", dest], app):
                run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", list_path, "-c:a", acodec, dest], app)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return exported, skipped

    if not dest or not os.path.isdir(dest):
        QMessageBox.warning(app, "Export", "Please select a valid destination folder.")
        return 0, skipped
    for i, shot, src in usable:
        out_full = os.path.join(dest, f"{safe_name(shot, i)}{ext}")
        if run_ffmpeg(["ffmpeg", "-y", "-i", src] + acodec_opts() + [out_full], app):
            exported += 1
    return exported, skipped


# --- Dialog ------------------------------------------------------------------
class ExportShotsDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Shots")
        self.app = app
        saved = app.settingsManager.get("export_config", {}) or {}

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Export type.
        self.typeCombo = QComboBox()
        for label, value in EXPORT_TYPE_LABELS:
            self.typeCombo.addItem(label, value)
        self._selectData(self.typeCombo, saved.get("type", EXPORT_VIDEO))
        form.addRow("Export type:", self.typeCombo)

        # Scope.
        self.scopeCombo = QComboBox()
        self.scopeCombo.addItem("All shots", "all")
        self.scopeCombo.addItem("Selected shots", "selected")
        self._selectData(self.scopeCombo, saved.get("scope", "all"))
        form.addRow("Shots:", self.scopeCombo)

        # Merge.
        self.mergeCheck = QCheckBox("Merge into a single output")
        self.mergeCheck.setChecked(bool(saved.get("merge", False)))
        form.addRow(self.mergeCheck)
        layout.addLayout(form)

        # --- Video options ---
        self.videoGroup = QGroupBox("Video options")
        vform = QFormLayout(self.videoGroup)
        self.containerCombo = QComboBox()
        self.containerCombo.addItems(VIDEO_CONTAINERS)
        self._selectText(self.containerCombo, saved.get("container", "mp4"))
        vform.addRow("Container:", self.containerCombo)
        self.videoCodecCombo = QComboBox()
        self.videoCodecCombo.addItems(VIDEO_CODECS)
        self._selectText(self.videoCodecCombo, saved.get("video_codec", "libx264"))
        vform.addRow("Video codec:", self.videoCodecCombo)
        adv = saved.get("advanced", {})
        self.videoBitrateSpin = QSpinBox()
        self.videoBitrateSpin.setRange(0, 100000)
        self.videoBitrateSpin.setSuffix(" kbps (0=auto)")
        self.videoBitrateSpin.setValue(adv.get("video_bitrate", 0))
        vform.addRow("Video bitrate:", self.videoBitrateSpin)
        self.crfSpin = QSpinBox()
        self.crfSpin.setRange(0, 51)
        self.crfSpin.setValue(adv.get("crf", 23))
        vform.addRow("CRF:", self.crfSpin)
        self.frameRateSpin = QDoubleSpinBox()
        self.frameRateSpin.setRange(0.0, 240.0)
        self.frameRateSpin.setDecimals(2)
        self.frameRateSpin.setValue(adv.get("frame_rate", 0.0))
        self.frameRateSpin.setSuffix(" fps (0=source)")
        vform.addRow("Frame rate:", self.frameRateSpin)
        self.presetCombo = QComboBox()
        self.presetCombo.addItems(["ultrafast", "superfast", "veryfast", "faster",
                                   "fast", "medium", "slow", "slower", "veryslow"])
        self._selectText(self.presetCombo, adv.get("preset", "medium"))
        vform.addRow("Preset (x264/x265):", self.presetCombo)
        self.audioCodecCombo = QComboBox()
        self.audioCodecCombo.addItems(["aac", "mp3", "ac3", "libopus", "copy"])
        self._selectText(self.audioCodecCombo, adv.get("audio_codec", "aac"))
        vform.addRow("Audio codec:", self.audioCodecCombo)
        self.vAudioBitrateSpin = QSpinBox()
        self.vAudioBitrateSpin.setRange(0, 640)
        self.vAudioBitrateSpin.setSuffix(" kbps (0=auto)")
        self.vAudioBitrateSpin.setValue(adv.get("audio_bitrate", 0))
        vform.addRow("Audio bitrate:", self.vAudioBitrateSpin)
        layout.addWidget(self.videoGroup)

        # --- Image options ---
        self.imageGroup = QGroupBox("Image options")
        iform = QFormLayout(self.imageGroup)
        self.imageFormatCombo = QComboBox()
        self.imageFormatCombo.addItems(IMAGE_FORMATS)
        self._selectText(self.imageFormatCombo, saved.get("image_format", "png"))
        iform.addRow("Image format:", self.imageFormatCombo)
        self.imageQualitySpin = QSpinBox()
        self.imageQualitySpin.setRange(1, 100)
        self.imageQualitySpin.setValue(saved.get("image_quality", 90))
        self.imageQualitySpin.setToolTip("Applies to JPG/WebP.")
        iform.addRow("Quality (jpg/webp):", self.imageQualitySpin)
        self.framePositionCombo = QComboBox()
        self.framePositionCombo.addItem("First frame", "first")
        self.framePositionCombo.addItem("Middle frame", "middle")
        self.framePositionCombo.addItem("Last frame", "last")
        self._selectData(self.framePositionCombo, saved.get("frame_position", "first"))
        self.framePositionRow = iform.rowCount()
        iform.addRow("Frame (single, from video):", self.framePositionCombo)
        self.seqFpsSpin = QDoubleSpinBox()
        self.seqFpsSpin.setRange(0.0, 240.0)
        self.seqFpsSpin.setDecimals(2)
        self.seqFpsSpin.setValue(saved.get("sequence_fps", 0.0))
        self.seqFpsSpin.setSuffix(" fps (0=all frames)")
        iform.addRow("Sequence rate (from video):", self.seqFpsSpin)
        layout.addWidget(self.imageGroup)

        # --- Audio options ---
        self.audioGroup = QGroupBox("Audio options")
        aform = QFormLayout(self.audioGroup)
        self.audioFormatCombo = QComboBox()
        self.audioFormatCombo.addItems(list(AUDIO_FORMATS.keys()))
        self._selectText(self.audioFormatCombo, saved.get("audio_format", "wav"))
        aform.addRow("Audio format:", self.audioFormatCombo)
        self.audioBitrateSpin = QSpinBox()
        self.audioBitrateSpin.setRange(0, 640)
        self.audioBitrateSpin.setSuffix(" kbps (0=auto)")
        self.audioBitrateSpin.setValue(saved.get("audio_bitrate", 192))
        aform.addRow("Audio bitrate:", self.audioBitrateSpin)
        layout.addWidget(self.audioGroup)

        # Destination.
        destForm = QFormLayout()
        self.destEdit = QLineEdit()
        destBtnLayout = QHBoxLayout()
        destBtn = QPushButton("Browse...")
        destBtnLayout.addWidget(self.destEdit)
        destBtnLayout.addWidget(destBtn)
        destForm.addRow("Destination:", destBtnLayout)
        self.destHint = QLabel("")
        self.destHint.setStyleSheet("color: gray;")
        destForm.addRow("", self.destHint)
        layout.addLayout(destForm)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        destBtn.clicked.connect(self.onBrowse)
        self.typeCombo.currentIndexChanged.connect(self.updateVisibility)
        self.mergeCheck.stateChanged.connect(self.updateVisibility)
        self.updateVisibility()

    # -- small helpers --
    def _selectData(self, combo, value):
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _selectText(self, combo, text):
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def currentType(self):
        return self.typeCombo.currentData()

    def isSingleFileOutput(self):
        """True when the chosen type+merge produces one file (not a folder)."""
        return self.mergeCheck.isChecked() and self.currentType() in (EXPORT_VIDEO, EXPORT_AUDIO)

    def updateVisibility(self):
        etype = self.currentType()
        self.videoGroup.setVisible(etype == EXPORT_VIDEO)
        self.imageGroup.setVisible(etype in (EXPORT_FRAME, EXPORT_SEQUENCE))
        self.audioGroup.setVisible(etype == EXPORT_AUDIO)
        # Frame-position only for single frame; sequence-rate only for sequence.
        self.framePositionCombo.setVisible(etype == EXPORT_FRAME)
        self.seqFpsSpin.setVisible(etype == EXPORT_SEQUENCE)
        # Merge only makes sense for video/audio.
        self.mergeCheck.setEnabled(etype in (EXPORT_VIDEO, EXPORT_AUDIO, EXPORT_SEQUENCE))
        if etype not in (EXPORT_VIDEO, EXPORT_AUDIO, EXPORT_SEQUENCE):
            self.mergeCheck.setChecked(False)
        if etype == EXPORT_SEQUENCE:
            self.mergeCheck.setText("Merge into one folder (else per-shot subfolders)")
        else:
            self.mergeCheck.setText("Merge into a single output")
        self.destHint.setText(
            "Destination: a single file" if self.isSingleFileOutput()
            else "Destination: a folder")
        self.adjustSize()

    def onBrowse(self):
        if self.isSingleFileOutput():
            if self.currentType() == EXPORT_AUDIO:
                label = AUDIO_FORMATS.get(self.audioFormatCombo.currentText())
                ext = (label[1] if label else ".wav").lstrip(".")
                flt = f"Audio (*.{ext});;All Files (*)"
            else:
                ext = self.containerCombo.currentText()
                flt = f"Video (*.{ext});;All Files (*)"
            path, _ = QFileDialog.getSaveFileName(self, "Select Output File", self.destEdit.text(), flt)
            if path:
                if not os.path.splitext(path)[1]:
                    path += f".{ext}"
                self.destEdit.setText(path)
        else:
            folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.destEdit.text())
            if folder:
                self.destEdit.setText(folder)

    def getConfig(self):
        return {
            "type": self.currentType(),
            "scope": self.scopeCombo.currentData(),
            "merge": self.mergeCheck.isChecked(),
            "dest": self.destEdit.text().strip(),
            "container": self.containerCombo.currentText().strip(),
            "video_codec": self.videoCodecCombo.currentText().strip(),
            "image_format": self.imageFormatCombo.currentText().strip(),
            "image_quality": self.imageQualitySpin.value(),
            "frame_position": self.framePositionCombo.currentData(),
            "sequence_fps": self.seqFpsSpin.value(),
            "audio_format": self.audioFormatCombo.currentText().strip(),
            "audio_bitrate": self.audioBitrateSpin.value() or None,
            "advanced": {
                "video_bitrate": self.videoBitrateSpin.value() or None,
                "crf": self.crfSpin.value(),
                "frame_rate": self.frameRateSpin.value() or None,
                "preset": self.presetCombo.currentText().strip(),
                "audio_codec": self.audioCodecCombo.currentText().strip(),
                "audio_bitrate": self.vAudioBitrateSpin.value() or None,
            },
        }
