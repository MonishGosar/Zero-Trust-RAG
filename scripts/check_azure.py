"""Use synthetic input to verify the configured Azure deployments; never print secrets."""

from backend.azure import AzureProvider
from backend.config import Settings
from backend.main import safe_error


def main():
    settings = Settings()
    if settings.missing:
        print("Missing settings: " + ", ".join(settings.missing))
        raise SystemExit(1)
    provider = AzureProvider(settings)
    try:
        vectors = provider.embed(["Folio connection check."])
        print(f"Embeddings connected: {len(vectors[0])} dimensions.")
        response = provider.client().chat.completions.create(
            model=settings.azure_openai_chat_deployment,
            messages=[{"role": "user", "content": "Reply with the word connected."}],
        )
        if not response.choices[0].message.content:
            raise ValueError("Azure returned no answer.")
        print("Chat deployment connected.")
    except Exception as exc:
        print(safe_error(exc))
        raise SystemExit(1) from None
    finally:
        provider.close()


if __name__ == "__main__":
    main()
