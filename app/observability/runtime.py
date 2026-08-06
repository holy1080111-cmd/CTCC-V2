from app.demo_automation.runtime import safe_demo_automation
from app.observability.service import DemoObservabilityService


demo_observability = DemoObservabilityService(automation=safe_demo_automation)
