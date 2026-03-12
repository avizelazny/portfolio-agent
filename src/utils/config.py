import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


@dataclass
class Config:
    """All runtime configuration loaded from environment variables.

    Populated once by get_config() and cached for the lifetime of the process.
    See .env.example for the full list of supported variables.
    """

    db_host: str
    db_port: str
    db_name: str
    db_user: str
    db_password: str
    redis_host: str
    redis_port: str
    bank_discount_api_key: str
    bank_discount_base_url: str
    tase_api_key: str
    tase_base_url: str
    anthropic_api_key: str
    voyage_api_key: str
    report_recipient_email: str
    ses_sender_email: str
    email_host: str
    email_port: int
    email_use_tls: bool
    aws_region: str
    s3_reports_bucket: str
    s3_rawdata_bucket: str
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str

    @property
    def db_url(self) -> str:
        """Construct a PostgreSQL connection URL from individual DB fields."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Load environment variables and return the singleton Config instance.

    Calls load_dotenv() internally so callers do not need to manage it.
    The result is cached — subsequent calls return the same object.

    Returns:
        A fully populated Config dataclass instance.
    """
    load_dotenv()
    return Config(
        db_host=os.getenv("DB_HOST", "localhost"),
        db_port=os.getenv("DB_PORT", "5432"),
        db_name=os.getenv("DB_NAME", "portfolio_agent"),
        db_user=os.getenv("DB_USER", "agent_admin"),
        db_password=os.getenv("DB_PASSWORD", "localdev123"),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=os.getenv("REDIS_PORT", "6379"),
        bank_discount_api_key=os.getenv("BANK_DISCOUNT_API_KEY", ""),
        bank_discount_base_url=os.getenv("BANK_DISCOUNT_BASE_URL", ""),
        tase_api_key=os.getenv("TASE_API_KEY", ""),
        tase_base_url=os.getenv("TASE_BASE_URL", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        voyage_api_key=os.getenv("VOYAGE_API_KEY", ""),
        report_recipient_email=os.getenv("REPORT_RECIPIENT_EMAIL", ""),
        ses_sender_email=os.getenv("SES_SENDER_EMAIL", "agent@local.dev"),
        email_host=os.getenv("EMAIL_HOST", "localhost"),
        email_port=int(os.getenv("EMAIL_PORT", "1025")),
        email_use_tls=os.getenv("EMAIL_USE_TLS", "false").lower() == "true",
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        s3_reports_bucket=os.getenv("S3_REPORTS_BUCKET", "portfolio-agent-reports"),
        s3_rawdata_bucket=os.getenv("S3_RAWDATA_BUCKET", "portfolio-agent-rawdata"),
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://localhost:9000"),
        s3_access_key=os.getenv("S3_ACCESS_KEY", "minioadmin"),
        s3_secret_key=os.getenv("S3_SECRET_KEY", "minioadmin123"),
    )
