from pydantic import BaseModel, ConfigDict


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    settings: dict[str, str]


class SettingUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: str
