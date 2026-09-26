"""
Реєстрація пайплайнів. ВАЖЛИВО: кожен новий пайплайн треба імпортувати тут,
інакше він не потрапить у реєстр і конфіг з його type впаде з помилкою.

Блоки конструктора і їх зв'язки (provides -> requires):

    target_search  →  target  →  attack
                              →  loot
    pet_heal       →  pet
    periodic_keys     (ні від кого не залежить)
"""
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext, Frame  # noqa: F401
from app.pipelines.actions import (  # noqa: F401
    Action, ClickAt, PipelineResult, PressKey, TypeText, Wait,
)
from app.pipelines.shared import (  # noqa: F401
    SHARED_ALT, SHARED_BUSY, SHARED_PET, SHARED_PET_FOOD, SHARED_PLAYER, SHARED_POS, SHARED_TARGET,
    Altitude, Position, TargetInfo, busy_reasons, read_altitude, read_player, read_position,
    read_target,
    set_busy, write_target,
)
from app.pipelines.registry import (  # noqa: F401
    build_pipeline, config_schema, get_pipeline_class, known_types, register,
)
from app.pipelines.wiring import describe_wiring, order_pipelines  # noqa: F401

from app.pipelines.target_search import TargetSearchPipeline  # noqa: F401
from app.pipelines.attack import AttackPipeline  # noqa: F401
from app.pipelines.loot import LootPipeline  # noqa: F401
from app.pipelines.pet import PetHealPipeline  # noqa: F401
from app.pipelines.pet_feed import PetFeedPipeline  # noqa: F401
from app.pipelines.pet_summon import PetSummonPipeline  # noqa: F401
from app.pipelines.periodic import PeriodicKeysPipeline  # noqa: F401
from app.pipelines.repair import RepairPipeline  # noqa: F401
from app.pipelines.dialog_guard import DialogGuardPipeline  # noqa: F401
from app.pipelines.position import PositionPipeline  # noqa: F401
from app.pipelines.altitude import AltitudePipeline  # noqa: F401
from app.pipelines.return_home import ReturnHomePipeline  # noqa: F401
from app.pipelines.heal import HealPipeline  # noqa: F401
from app.pipelines.altitude_hold import AltitudeHoldPipeline  # noqa: F401
from app.pipelines.sell import SellPipeline  # noqa: F401
from app.pipelines.death_return import DeathReturnPipeline  # noqa: F401

__all__ = [
    "Pipeline", "PipelineConfig", "PipelineContext", "Frame",
    "Action", "ClickAt", "PipelineResult", "PressKey", "TypeText", "Wait",
    "SHARED_PET", "SHARED_PET_FOOD", "SHARED_TARGET", "TargetInfo", "read_target", "write_target",
    "build_pipeline", "config_schema", "get_pipeline_class", "known_types", "register",
    "describe_wiring", "order_pipelines",
    "TargetSearchPipeline", "AttackPipeline", "LootPipeline",
    "PetHealPipeline", "PetFeedPipeline", "PetSummonPipeline", "PeriodicKeysPipeline", "RepairPipeline", "DialogGuardPipeline",
]
