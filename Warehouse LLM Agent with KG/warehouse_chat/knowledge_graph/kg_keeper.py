import os
from rdflib import Graph, Namespace, URIRef, Literal

class KGKeeper:
    """Wrapper around the corrected warehouse_kg ontology."""

    def __init__(self, path="WarehouseKG.rdf"):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(base_dir, path)
        file_uri = "file:///" + full_path.replace("\\", "/")

        print(f"[KG] Loading ontology from {file_uri}")

        self.graph = Graph()
        self.graph.parse(file_uri, format="xml")

        self.W = Namespace(
            "http://www.semanticweb.org/hindm/ontologies/2025/11/warehouse_kg#"
        )

    # ------------------------------------------------------------
    # IRI resolution: modules/boxes/functions are named individuals
    # ------------------------------------------------------------
    def iri(self, name: str):
        """Return the full IRI for a warehouse entity."""
        return URIRef(self.W[str(name)])

    # --------------------------
    # Existence
    # --------------------------
    def entity_exists(self, name: str):
        iri = self.iri(name)
        return (iri, None, None) in self.graph

    def module_exists(self, name: str) -> bool:
        return self.entity_exists(name)

    # --------------------------
    # Module type(s)
    # --------------------------
    def module_type(self, module: str):
        iri = self.iri(module)
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?t WHERE {{
            <{iri}> rdf:type ?t .
        }}
        """
        return [str(r[0]) for r in self.graph.query(q)]

    # --------------------------
    # Module state (Operational_State)
    # --------------------------
    def module_state(self, module: str):
        iri = self.iri(module)
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?s WHERE {{
            OPTIONAL {{ <{iri}> w:hasState ?s . }}
        }}
        """
        rows = list(self.graph.query(q))
        return str(rows[0][0]) if rows and rows[0][0] else None

    # --------------------------
    # Position retrieval
    # --------------------------
    def module_position(self, module: str):
        iri = self.iri(module)
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?x ?y ?z WHERE {{
            <{iri}> w:hasPosition ?p .
            OPTIONAL {{ ?p w:x ?x . }}
            OPTIONAL {{ ?p w:y ?y . }}
            OPTIONAL {{ ?p w:z ?z . }}
        }}
        """
        rows = list(self.graph.query(q))
        if not rows:
            return None
        x, y, z = rows[0]
        return float(x), float(y), float(z)

    # --------------------------
    # Neighbors
    # --------------------------
    def neighbors(self, module: str):
        iri = self.iri(module)
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?n WHERE {{
            <{iri}> w:hasNeighbor ?n .
        }}
        """
        return [str(r[0]).split("#")[-1] for r in self.graph.query(q)]

    def are_neighbors(self, a: str, b: str) -> bool:
        ia, ib = self.iri(a), self.iri(b)
        q = f"""
        PREFIX w: <{self.W}>
        ASK {{
            <{ia}> w:hasNeighbor <{ib}> .
        }}
        """
        result = list(self.graph.query(q))
        return bool(result and result[0])

    # --------------------------
    # Reachability via hasNeighbor+
    # --------------------------
    def reachable(self, start: str, goal: str) -> bool:
        ia, ib = self.iri(start), self.iri(goal)
        q = f"""
        PREFIX w: <{self.W}>
        ASK {{
            <{ia}> w:hasNeighbor+ <{ib}> .
        }}
        """
        result = list(self.graph.query(q))
        return bool(result and result[0])

    # --------------------------
    # Box queries
    # --------------------------
    def known_box_colors(self):
        q = f"""
        PREFIX w: <{self.W}>
        SELECT DISTINCT ?c WHERE {{
            ?b rdf:type w:Box ;
               w:boxColor ?c .
        }}
        """
        return [str(r[0]) for r in self.graph.query(q)]

    def boxes(self):
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?b WHERE {{
            ?b rdf:type w:Box .
        }}
        """
        return [str(r[0]).split("#")[-1] for r in self.graph.query(q)]

    def get_box_pose(self, box_name: str):
        iri = self.iri(box_name)
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?x ?y ?z WHERE {{
            <{iri}> w:hasPosition ?p .
            OPTIONAL {{ ?p w:x ?x . }}
            OPTIONAL {{ ?p w:y ?y . }}
            OPTIONAL {{ ?p w:z ?z . }}
        }}
        """
        rows = list(self.graph.query(q))
        if not rows: 
            return None
        x, y, z = rows[0]
        return float(x), float(y), float(z)

    # --------------------------
    # List all callable functions
    # --------------------------
    def list_functions(self):
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?fn WHERE {{
            ?fn rdf:type/rdfs:subClassOf* w:Callable_Function .
        }}
        """
        return [str(r[0]).split("#")[-1] for r in self.graph.query(q)]

    def list_function_inputs(self, fn_name: str):
        iri = self.iri(fn_name)
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?inp WHERE {{
            <{iri}> w:expectsInput ?inp .
        }}
        """
        return [str(r[0]).split("#")[-1] for r in self.graph.query(q)]

    # --------------------------
    # Validation used by tools
    # --------------------------
    def validate_operation(self, start: str, goal: str):
        if not self.module_exists(start):
            return False, f"Module '{start}' does not exist."

        if not self.module_exists(goal):
            return False, f"Module '{goal}' does not exist."

        s_state = self.module_state(start)
        g_state = self.module_state(goal)

        if s_state and "Offline" in s_state:
            return False, f"Start module '{start}' is offline."

        if g_state and "Offline" in g_state:
            return False, f"Goal module '{goal}' is offline."

        if not self.reachable(start, goal):
            return False, f"No path available in KG from {start} → {goal}."

        return True, "Valid operation."
    
    # --------------------------
    # last order query
    # --------------------------
    
    def get_last_operation(self):
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?op ?ts WHERE {{
        ?op a w:TransportOperation ;
            w:executedAt ?ts .
        }}
        ORDER BY DESC(?ts)
        LIMIT 1
        """
        rows = list(self.graph.query(q))
        if not rows:
            return None
        return rows[0][0], rows[0][1]   # (operation IRI, timestamp)
    
    # --------------------------
    # last order details
    # --------------------------
    
    def get_operation_details(self, op_iri):
        q = f"""
        PREFIX w: <{self.W}>
        SELECT ?start ?goal ?box ?cid ?ts ?success WHERE {{
            OPTIONAL {{ <{op_iri}> w:hasStartModule ?start . }}
            OPTIONAL {{ <{op_iri}> w:hasGoalModule ?goal . }}
            OPTIONAL {{ <{op_iri}> w:movedBox      ?box . }}
            OPTIONAL {{ <{op_iri}> w:correlationID ?cid . }}
            OPTIONAL {{ <{op_iri}> w:executedAt    ?ts . }}
            OPTIONAL {{ <{op_iri}> w:IsFinishedSuccessfully ?success . }}
        }}
        LIMIT 1
        """
        rows = list(self.graph.query(q))
        if not rows:
            return None

        s, g, b, cid, ts, sc = rows[0]

        def clean(x):
            if x is None:
                return None
            s = str(x)
            return s.split("#")[-1] if "#" in s else s

        return {
            "start": clean(s),
            "goal": clean(g),
            "box": clean(b),
            "correlation_id": clean(cid),
            "timestamp": str(ts),
            "success": (str(sc).lower() == "true")
        }


