from rdflib import Graph, Namespace, Literal, RDF, XSD, URIRef
from datetime import datetime

# Load ontology once
KG_PATH = "knowledge_graph/WarehouseKG.rdf"

g = Graph()
g.parse(KG_PATH)

# Define namespaces (adjust prefix if different)
WH = Namespace("http://www.semanticweb.org/hindm/ontologies/2025/11/warehouse_kg#")
g.bind("wh", WH)


def log_transport_operation(
    *,
    correlation_id: str,
    start_module: str,
    goal_module: str,
    box_individual: str,
    success: bool,
    timestamp: float | None = None,
):
    """
    Create a new TransportOperation individual in the ontology
    and attach all semantic metadata.
    """

    # Generate operation individual name
    op_name = f"operation_{correlation_id}"
    op_uri = WH[op_name]

    # Create the individual of class TransportOperation
    g.add((op_uri, RDF.type, WH.TransportOperation))

    # --- Object properties ------------------------------------------
    g.add((op_uri, WH.hasStartModule, WH[start_module]))
    g.add((op_uri, WH.hasGoalModule, WH[goal_module]))
    g.add((op_uri, WH.movedBox, WH[box_individual]))

    # --- Data properties --------------------------------------------
    g.add((op_uri, WH.correlationID, Literal(correlation_id, datatype=XSD.string)))

    dt = datetime.utcfromtimestamp(timestamp) if timestamp else datetime.utcnow()
    g.add((op_uri, WH.executedAt, Literal(dt.isoformat(), datatype=XSD.dateTime)))
    
    g.add((op_uri,
           WH.IsFinishedSuccessfully,
           Literal(success, datatype=XSD.boolean)))

    # --- Save ontology ----------------------------------------------
    g.serialize(KG_PATH, format="xml")

    print(f"[KG] Logged operation {op_name}")
    return op_name
