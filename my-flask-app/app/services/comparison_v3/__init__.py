"""Comparison V3 (``comparison-v3/1``): every vehicle fact comes from TRIPY.

TRIPY (``POST {TRIPY_BASE_URL}/api/facts/v1/vehicles``, contract ``vehicle-facts/1``) returns one merged,
provenance-tagged record per exact variant: the government Level 1.5 row plus the open-data facts it admitted.
This repository never reads MILO, EEA, EPA, CVS, NRCan or ADEME itself, and V3 makes no grounded Gemini enrichment.

Invariant (unchanged from V2): code compares and composes, JEV makes narrow judgments, and the LLM only explains.

The V3 modules never import ``comparison_v2.official_enrichment``, ``comparison_v2.grounding`` or
``comparison_v2.source_registry`` (tested).
"""
