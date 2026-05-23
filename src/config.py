from dataclasses import dataclass

@dataclass
class Settings:
    project_name: str = "ml-incident-response-playbook"
    severity_default: str = "SEV-3"
