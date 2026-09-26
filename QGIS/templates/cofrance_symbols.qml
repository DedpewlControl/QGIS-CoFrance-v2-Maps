<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="Symbology|Fields">
  <renderer-v2 type="categorizedSymbol" attr="symbol_type" symbollevels="0" referencescale="-1" enableorderby="0" forceraster="0">
    <categories>
      <category value="vor_dme" label="vor_dme" symbol="0" render="true" type="string"/>
      <category value="dme" label="dme" symbol="1" render="true" type="string"/>
      <category value="vor" label="vor" symbol="2" render="true" type="string"/>
      <category value="ndb" label="ndb" symbol="3" render="true" type="string"/>
      <category value="tacan" label="tacan" symbol="4" render="true" type="string"/>
      <category value="vortac" label="vortac" symbol="5" render="true" type="string"/>
      <category value="" label="Other symbols" symbol="6" render="true" type="string"/>
    </categories>
    <symbols>
    <symbol name="0" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SvgMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="angle" value="0" type="double"/>
          <Option name="fixedAspectRatio" value="0" type="double"/>
          <Option name="name" value="svg/VOR_DME.svg" type="QString"/>
          <Option name="size" value="6" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    <symbol name="1" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SvgMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="angle" value="0" type="double"/>
          <Option name="fixedAspectRatio" value="0" type="double"/>
          <Option name="name" value="svg/DME.svg" type="QString"/>
          <Option name="size" value="6" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    <symbol name="2" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SvgMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="angle" value="0" type="double"/>
          <Option name="fixedAspectRatio" value="0" type="double"/>
          <Option name="name" value="svg/VOR.svg" type="QString"/>
          <Option name="size" value="6" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    <symbol name="3" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SvgMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="angle" value="0" type="double"/>
          <Option name="fixedAspectRatio" value="0" type="double"/>
          <Option name="name" value="svg/NDB.svg" type="QString"/>
          <Option name="size" value="6" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    <symbol name="4" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SvgMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="angle" value="0" type="double"/>
          <Option name="fixedAspectRatio" value="0" type="double"/>
          <Option name="name" value="svg/TACAN.svg" type="QString"/>
          <Option name="size" value="6" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    <symbol name="5" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SvgMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="angle" value="0" type="double"/>
          <Option name="fixedAspectRatio" value="0" type="double"/>
          <Option name="name" value="svg/VORTAC.svg" type="QString"/>
          <Option name="size" value="6" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    <symbol name="6" type="marker" alpha="1" clip_to_extent="1" force_rhr="0">
      <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
        <Option type="Map">
          <Option name="name" value="circle" type="QString"/>
          <Option name="color" value="128,128,128,255" type="QString"/>
          <Option name="outline_color" value="35,35,35,255" type="QString"/>
          <Option name="size" value="4" type="double"/>
          <Option name="size_unit" value="MM" type="QString"/>
        </Option>
      </layer>
    </symbol>
    </symbols>
  </renderer-v2>
  <fieldConfiguration>
    <field name="name" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="symbol_type" configurationFlags="None">
      <editWidget type="ValueMap">
        <config>
          <Option type="Map">
            <Option name="map" type="List">
              <Option type="Map">
                <Option name="point" value="point" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="circle" value="circle" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="square" value="square" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="diamond" value="diamond" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="diamond_cross" value="diamond_cross" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="circle_cross" value="circle_cross" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="cross" value="cross" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="cross_large" value="cross_large" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="x" value="x" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="asterix" value="asterix" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="triangle_hollow" value="triangle_hollow" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="triangle_filled" value="triangle_filled" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="triangle_hollow_thick_bottom_border" value="triangle_hollow_thick_bottom_border" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="triangle_with_circle_rings" value="triangle_with_circle_rings" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="circle_with_outer_rings" value="circle_with_outer_rings" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="vor" value="vor" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="vor_dme" value="vor_dme" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="dme" value="dme" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="ndb" value="ndb" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="navaid" value="navaid" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="tacan" value="tacan" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="vortac" value="vortac" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="vor_classic" value="vor_classic" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="ndb_classic" value="ndb_classic" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="aerodrome" value="aerodrome" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="aerodrome_paved" value="aerodrome_paved" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="aerodrome_ticks" value="aerodrome_ticks" type="QString"/>
              </Option>
              <Option type="Map">
                <Option name="aerodrome_paved_ticks" value="aerodrome_paved_ticks" type="QString"/>
              </Option>
            </Option>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="z_index" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="zoomin" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="zoomout" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_icao" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_arr" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_dep" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_unactive_icao" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_unactive_arr" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_unactive_dep" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_sector_me" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
    <field name="activation_sector_others" configurationFlags="None">
      <editWidget type="TextEdit">
        <config>
          <Option type="Map">
            <Option name="IsMultiline" value="false" type="bool"/>
            <Option name="UseHtml" value="false" type="bool"/>
          </Option>
        </config>
      </editWidget>
    </field>
  </fieldConfiguration>
  <aliases>
    <alias name="" index="0" field="name"/>
    <alias name="" index="1" field="symbol_type"/>
    <alias name="" index="2" field="z_index"/>
    <alias name="" index="3" field="zoomin"/>
    <alias name="" index="4" field="zoomout"/>
    <alias name="" index="5" field="activation_icao"/>
    <alias name="" index="6" field="activation_arr"/>
    <alias name="" index="7" field="activation_dep"/>
    <alias name="" index="8" field="activation_unactive_icao"/>
    <alias name="" index="9" field="activation_unactive_arr"/>
    <alias name="" index="10" field="activation_unactive_dep"/>
    <alias name="" index="11" field="activation_sector_me"/>
    <alias name="" index="12" field="activation_sector_others"/>
  </aliases>
</qgis>
