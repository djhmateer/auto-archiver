"""Thumbnail Enricher for generating visual previews of video files.

The `ThumbnailEnricher` processes video files in `Metadata` objects and
creates evenly distributed thumbnail images. These thumbnails provide
visual snapshots of the video's keyframes, helping users preview content
and identify important moments without watching the entire video.

"""

import ffmpeg
import os
from auto_archiver.utils.custom_logger import logger

from auto_archiver.core import Enricher
from auto_archiver.core import Media, Metadata
from auto_archiver.utils.misc import random_str


class ThumbnailEnricher(Enricher):
    """
    Generates thumbnails for all the media
    """

    def enrich(self, to_enrich: Metadata) -> None:
        """
        Uses or reads the video duration to generate thumbnails
        Calculates how many thumbnails to generate and at which timestamps based on the video duration, the number of thumbnails per minute and the max number of thumbnails.
        Thumbnails are equally distributed across the video duration.
        """
        logger.debug("Generating thumbnails")
        for m_id, m in enumerate(to_enrich.media[::]):
            if m.is_video():
                folder = os.path.join(self.tmp_dir, random_str(24))
                os.makedirs(folder, exist_ok=True)
                logger.debug(f"Generating thumbnails for {m.filename}")
                duration = m.get("duration")

                try:
                    probe = ffmpeg.probe(m.filename)
                    video_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
                    # some containers (e.g. webm) don't set duration on the video stream, only on the overall format
                    duration = float(video_stream.get("duration") or probe["format"]["duration"])
                    to_enrich.media[m_id].set("duration", duration)
                except Exception as e:
                    logger.warning(f"Failed to get duration with FFMPEG from {m.filename}: {e}")

                if not duration or type(duration) not in [float, int] or duration <= 0:
                    logger.warning(f"Cannot generate thumbnails for {m.filename} without valid duration")
                    continue

                num_thumbs = int(min(max(1, (duration / 60) * self.thumbnails_per_minute), self.max_thumbnails))
                timestamps = [duration / (num_thumbs + 1) * i for i in range(1, num_thumbs + 1)]

                thumbnails_media = []
                for index, timestamp in enumerate(timestamps):
                    output_path = os.path.join(folder, f"out{index}.jpg")
                    try:
                        # capture stderr so a failing thumbnail is reported instead of aborting the whole enricher
                        ffmpeg.input(m.filename, ss=timestamp).filter("scale", 512, -1).output(
                            output_path, vframes=1, loglevel="error"
                        ).run(capture_stdout=True, capture_stderr=True)

                        if not os.path.exists(output_path):
                            logger.info(f"thumbnail {index} for media {m.filename} was not created")
                            continue
                        thumbnails_media.append(
                            Media(filename=output_path)
                            .set("id", f"thumbnail_{index}")
                            .set("timestamp", "%.3fs" % timestamp)
                        )
                    except ffmpeg.Error as e:
                        stderr = e.stderr.decode(errors="replace") if e.stderr else e
                        # DM 24th Sep 26 - a broken/truncated file fails the same way for every timestamp,
                        # so stop here rather than logging the same error up to max_thumbnails times
                        logger.error(
                            f"ffmpeg error creating thumbnail {index} for {m.filename}, skipping remaining thumbnails: {stderr}"
                        )
                        break
                    except Exception as e:
                        logger.error(f"error creating thumbnail {index} for media: {e}")

                to_enrich.media[m_id].set("thumbnails", thumbnails_media)
