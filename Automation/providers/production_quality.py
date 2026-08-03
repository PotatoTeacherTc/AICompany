from providers.models import UsageMetadata


class FakeProductionProvider:
    provider_name="fake-production";is_paid=False
    def generate(self,brief,count):
        values=[]
        for index in range(count):
            number=index+1
            values.append({"concept_variant":f"concept-{number}","text_direction":f"wording-{number}-opening-{number}","visual_direction":f"subject-{number}:composition-{number}:angle-{number}:palette-{number}:lighting-{number}","platform_strategy":f"youtube-title-{number}|naver-body-{number}|thumbnail-{number}","prompt_fingerprint":f"fingerprint-{number}","variation_metadata":{"seed":100+number}})
        return {"candidates":values,"usage":UsageMetadata(estimated_cost_usd=0.0)}


class FakeQualityProvider:
    provider_name="fake-quality";is_paid=False
    def review(self,candidate,kind):
        return {"summary":f"Structured {kind.lower()} review.","checks":{"required_outputs":True,"prohibited_patterns":True,"missing_inputs":False,"repeated_wording":candidate.variant_index==1,"platform_mismatch":False,"technical_validation":True}}
