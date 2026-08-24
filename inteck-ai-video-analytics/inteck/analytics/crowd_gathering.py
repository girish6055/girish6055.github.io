"""4. Gathering of more than N people (default: more than 2)."""
from typing import List

import numpy as np

from ..config import AnalyticConfig, CameraConfig
from ..geometry import pairwise_clusters
from .base import Analytic, DwellTracker, FrameContext, Services
from .draw import draw_label, draw_polygon


class CrowdGatheringAnalytic(Analytic):
    type_name = "crowd_gathering"
    title = "Gathering of more than 2 people"
    wanted_labels = ("person",)

    def __init__(self, camera: CameraConfig, config: AnalyticConfig, services: Services) -> None:
        super().__init__(camera, config, services)
        self.max_people = int(config.get("max_people", 2))
        self.cluster_radius = float(config.get("cluster_radius_px", 220))
        self.dwell = DwellTracker(self.dwell_seconds or 15.0, grace_seconds=3.0)
        self.largest_group = 0

    def process(self, ctx: FrameContext) -> None:
        people = self.detections_in_zone(ctx, ["person"])
        groups: List[List[int]] = []
        if self.cluster_radius > 0:
            clusters = pairwise_clusters([p.center for p in people], self.cluster_radius)
            groups = [group for group in clusters if len(group) > self.max_people]
        elif len(people) > self.max_people:
            groups = [list(range(len(people)))]

        self.largest_group = max([len(g) for g in groups], default=len(people) if not groups else 0)
        # Key each group by its member track ids so a persisting huddle keeps
        # its dwell timer even as members shuffle position.
        keys = []
        group_lookup = {}
        for group in groups:
            ids = tuple(sorted(people[i].track_id for i in group if people[i].track_id is not None))
            if not ids:
                continue
            keys.append(ids)
            group_lookup[ids] = group

        breached = self.dwell.update(keys, ctx.ts)
        self.status_text = f"{len(people)} people, largest group {self.largest_group}"
        for ids in sorted(breached, key=len, reverse=True):
            group = group_lookup.get(ids, [])
            self.emit(
                ctx,
                title=f"Gathering of {len(ids)} people",
                message=(
                    f"{len(ids)} people gathered within {self.cluster_radius:.0f}px for "
                    f"{self.dwell.dwell_seconds:.0f}s (limit is {self.max_people})."
                ),
                track_ids=list(ids),
                meta={"group_size": len(ids), "limit": self.max_people, "people_in_zone": len(people)},
                dedupe_key="group",
            )
            break  # one alert per cooldown window is enough

    def overlay(self, frame: np.ndarray, ctx: FrameContext) -> None:
        polygon = ctx.zone_polygon(self.zone_name)
        breach = self.largest_group > self.max_people
        draw_polygon(frame, polygon, (0, 140, 255) if breach else (120, 120, 120),
                     label=f"GROUPING max={self.largest_group}", alpha=0.08)
        if breach:
            draw_label(frame, f"GATHERING {self.largest_group} PEOPLE", (12, 44), (0, 140, 255), 0.6)
