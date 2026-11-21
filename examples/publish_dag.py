"""Publish a causal diagram to Framework Cloud with fw.publish_dag().

Run it from a Framework project linked to Framework Cloud (FW_PROJECT_TOKEN in .env),
with networkx installed:

    pip install framework-py networkx
    python publish_dag.py

Does education improve health? Parental SES drives both, and so does the neighborhood
a person grew up in, which no survey measured. Income carries part of education's
effect, and an unmeasured common cause links income and health. Adjusting for
parental SES closes every backdoor path, so the total effect is identified.
"""

import inspect

import networkx as nx

import framework as fw


def education_health() -> nx.DiGraph:
    dag = nx.DiGraph()
    dag.add_node("education", role="exposure", label="Years of education")
    dag.add_node("health", role="outcome", label="Self-rated health")
    dag.add_node("parental SES", label="Parental SES")
    dag.add_node("income", label="Household income")
    dag.add_node("neighborhood", role="latent", label="Childhood neighborhood")
    dag.add_edges_from(
        [
            ("neighborhood", "parental SES"),
            ("neighborhood", "health"),
            ("parental SES", "education"),
            ("parental SES", "health"),
            ("education", "income"),
            ("education", "health"),
            ("income", "health"),
        ]
    )
    # An unmeasured common cause of income and health
    dag.add_edge("income", "health", bidirected=True)
    return dag


dag = education_health()
print(fw.dagitty(dag))

url = fw.publish_dag(
    dag,
    slug="education-health-dag",
    title="Education and health",
    description="Causal diagram for the effect of education on self-rated health.",
    code=inspect.getsource(education_health),
    analysis={"effect": "total", "adjustment_sets": [["parental SES"]]},
    visibility="unlisted",
    to="cloud",
)
print(url)
