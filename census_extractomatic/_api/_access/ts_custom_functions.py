# Following a recommendation here
# https://github.com/kayak/pypika/issues/349

from pypika import Table, CustomFunction
from pypika.enums import Comparator
from pypika.terms import BasicCriterion


class Comp(Comparator):
    match = "@@"

    def get_sql(self):
        return self.value


to_tsvector = CustomFunction("to_tsvector", ["text"])
to_tsquery = CustomFunction("to_tsquery", ["text"])
ts_rank_cd = CustomFunction("ts_rank_cd", ["column", "query"])

ts_unindexed = lambda col, q: BasicCriterion(Comp.match, to_tsvector(col), to_tsquery(q))
ts_indexed = lambda search_field, q: BasicCriterion(Comp.match, search_field, to_tsquery(q))


def prep_q_for_text_search(q):
    return q.replace(" ", " | ")


if __name__ == "__main__":
    table = Table("table")

    print(ts_unindexed(table.alpha, table.beta))
