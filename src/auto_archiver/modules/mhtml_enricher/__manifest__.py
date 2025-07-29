{
    "name": "MHTML Enricher",
    "type": ["enricher"],
    "entry_point": "mhtml_enricher::MhtmlEnricher",
    "requires_setup": False,
    "dependencies": {
        "python": ["loguru"],
    },
    "configs": {
    },
    "description": """
    Saves an mhtml file of the URL
    Note the user has to download the file and can't view it live (intentional Chrome security feature)
    """,
}
