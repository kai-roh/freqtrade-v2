"""
LLMEnhancedModel - LightGBM 기반 FreqAI 모델

기본 LightGBMRegressor를 상속하면서 LLM 피처를 활용하는 형태.
복잡도를 최소화해 디버깅을 쉽게 함.
"""

import logging
from typing import Any

from freqtrade.freqai.prediction_models.LightGBMRegressor import LightGBMRegressor

logger = logging.getLogger(__name__)


class LLMEnhancedModel(LightGBMRegressor):
    """
    LightGBM 기반 회귀 모델.
    피처에 LLM 감성 점수가 포함되어 있다고 가정 (KaiBaseStrategy에서 주입).

    추후 확장:
    - fit() 오버라이드해 LLM 가중치 조정
    - 앙상블 (LightGBM + CatBoost) 추가
    """

    def fit(self, data_dictionary: dict, dk: Any, **kwargs) -> Any:
        logger.info(
            f"[LLMEnhancedModel] Training on {len(data_dictionary['train_features'])} samples"
        )
        model = super().fit(data_dictionary, dk, **kwargs)
        # 피처 중요도 로깅
        try:
            importances = model.feature_importances_
            features = data_dictionary["train_features"].columns.tolist()
            top10 = sorted(
                zip(features, importances, strict=False), key=lambda x: x[1], reverse=True
            )[:10]
            logger.info(f"[LLMEnhancedModel] Top10 features: {top10}")
        except Exception as e:
            logger.warning(f"Feature importance log failed: {e}")
        return model
