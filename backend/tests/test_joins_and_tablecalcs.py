"""Join/relationship details and shelf-level (quick) table calculations."""

from __future__ import annotations

from app.migration import assess
from app.parser import parse_workbook

TWB = b"""<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <datasources>
    <datasource caption='Legacy Join' name='federated.legacy' version='18.1'>
      <connection class='federated'>
        <named-connections><named-connection name='sqlserver.1'><connection class='sqlserver' server='db' dbname='d' /></named-connection></named-connections>
        <relation join='left' type='join'>
          <clause type='join'>
            <expression op='='>
              <expression op='[Orders].[Order ID]' />
              <expression op='[Returns].[Order ID]' />
            </expression>
          </clause>
          <relation connection='sqlserver.1' name='Orders' table='[dbo].[Orders]' type='table' />
          <relation connection='sqlserver.1' name='Returns' table='[dbo].[Returns]' type='table' />
        </relation>
      </connection>
    </datasource>
    <datasource caption='Modern Model' name='federated.modern' version='18.1'>
      <connection class='federated'>
        <named-connections><named-connection name='sqlserver.2'><connection class='sqlserver' server='db' dbname='d' /></named-connection></named-connections>
        <relation connection='sqlserver.2' name='Orders' table='[dbo].[Orders]' type='table' />
      </connection>
      <object-graph>
        <objects>
          <object caption='Orders' id='Orders_1'/>
          <object caption='People' id='People_2'/>
        </objects>
        <relationships>
          <relationship>
            <expression op='='>
              <expression op='[Region (Orders)]' />
              <expression op='[Region (People)]' />
            </expression>
            <first-end-point object-id='Orders_1' />
            <second-end-point object-id='People_2' />
          </relationship>
        </relationships>
      </object-graph>
    </datasource>
    <datasource caption='Plain' name='federated.plain' version='18.1'>
      <connection class='federated'>
        <relation name='Only' table='[Only]' type='table' />
      </connection>
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='Share'>
      <table>
        <view>
          <datasources><datasource caption='Plain' name='federated.plain' /></datasources>
          <datasource-dependencies datasource='federated.plain'>
            <column datatype='real' name='[Sales]' role='measure' type='quantitative' />
            <column-instance column='[Sales]' derivation='Sum' name='[pcto:sum:Sales:qk:1]' pivot='key' type='quantitative'>
              <table-calc ordering-type='Columns' type='PctTotal' />
            </column-instance>
            <column-instance column='[Sales]' derivation='Sum' name='[sum:Sales:qk]' pivot='key' type='quantitative' />
          </datasource-dependencies>
        </view>
        <panes><pane><mark class='Bar' /></pane></panes>
      </table>
    </worksheet>
    <worksheet name='Plainsheet'>
      <table>
        <view>
          <datasources><datasource caption='Plain' name='federated.plain' /></datasources>
          <datasource-dependencies datasource='federated.plain'>
            <column datatype='real' name='[Sales]' role='measure' type='quantitative' />
            <column-instance column='[Sales]' derivation='Sum' name='[sum:Sales:qk]' pivot='key' type='quantitative' />
          </datasource-dependencies>
        </view>
        <panes><pane><mark class='Bar' /></pane></panes>
      </table>
    </worksheet>
  </worksheets>
</workbook>
"""


def parse():
    meta = parse_workbook(TWB, [])
    meta.pop("warnings")
    return meta


def test_legacy_join_shows_type_tables_and_keys():
    ds = next(d for d in parse()["datasources"] if d["caption"] == "Legacy Join")
    assert ds["joins"] == 1
    assert ds["join_details"] == ["left join Orders and Returns on [Orders].[Order ID] = [Returns].[Order ID]"]


def test_relationships_resolve_object_names():
    ds = next(d for d in parse()["datasources"] if d["caption"] == "Modern Model")
    assert ds["join_details"] == ["relationship Orders to People on [Region (Orders)] = [Region (People)]"]


def test_datasource_without_joins_has_no_details():
    ds = next(d for d in parse()["datasources"] if d["caption"] == "Plain")
    assert ds["join_details"] == []


def test_quick_table_calc_is_recorded_per_worksheet_field():
    rows = {r["worksheet"]: r for r in parse()["worksheet_fields"]}
    assert rows["Share"]["quick_table_calcs"] == ["PctTotal"]
    assert rows["Plainsheet"]["quick_table_calcs"] == []


def test_quick_table_calc_is_rated_high_and_only_where_present():
    meta = parse()
    result = assess(meta)
    flagged = [i for i in result["items"] if "quick_table_calc" in i["flags"]]
    assert [(i["scope"], i["name"], i["level"]) for i in flagged] == [("worksheet field", "Share: Sales", "high")]
    assert "PctTotal" in flagged[0]["reasons"][0]
    joins = {i["name"]: i for i in result["items"] if i["scope"] == "datasource"}
    assert "joins" in joins["Legacy Join"]["flags"] and "joins" in joins["Modern Model"]["flags"]
    assert "Plain" not in joins
