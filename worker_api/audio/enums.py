import enum
from sqlalchemy import Enum


class ContentType(enum.Enum):
    TEXT = "TEXT"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    IMAGE = "IMAGE"
    SOURCE_REFERENCE = "SOURCE_REFERENCE"


class PlanAudioType(enum.Enum):
    RECITATION = "RECITATION"
    INSTRUCTION = "INSTRUCTION"
    TEXT_READING = "TEXT_READING"


class MonlamVoiceName(str, enum.Enum):
    DOLKAR_LHASA_FEMALE = "dolkar_lhasa_female"
    YANGCHEN_LHASA_FEMALE = "yangchen_lhasa_female"
    DARJEEYALPHEL_LHASA_MALE = "darjeeyalphel_lhasa_male"
    HISTRY_LHASA_MALE = "histry_lhasa_male"
    SONAMTSERING_LHASA_MALE = "sonamtsering_lhasa_male"
    DOLMA_AMDO_FEMALE = "dolma_amdo_female"
    KID_AMDO_FEMALE = "kid_amdo_female"
    BUDDHAHISTORY_AMDO_MALE = "buddhahistory_amdo_male"
    HISTORY_AMDO_MALE = "history_amdo_male"
    KALSANG_GYATSO_AMDO_MALE = "kalsang_gyatso_amdo_male"
    KOTHEKE_KHAM_MALE = "kotheke_kham_male"
    TIBET_TONGUE_KHAM_MALE = "tibet_tongue_kham_male"
    TSERING_WANGMO_KHAM_FEMALE = "tsering_wangmo_kham_female"
    WANGDONTSO_KHAM_FEMALE = "wangdontso_kham_female"


class AudioJobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


ContentTypeEnum = Enum(ContentType)
PlanAudioTypeEnum = Enum(PlanAudioType)
