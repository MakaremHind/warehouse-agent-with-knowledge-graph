from rdflib import Graph

class WarehouseKG:
    def __init__(self, path="../Warehouse_ontology.rdf"):
        self.graph = Graph()
        print(f"[KG] Loading ontology: {path}")
        self.graph.parse(path)

    def sparql(self, query: str):
        try:
            result = self.graph.query(query)
            return [dict(row) for row in result]
        except Exception as e:
            return {"error": str(e)}
