-- CDC/ATSDR SVI 2022 (county). -999 is the documented "missing" sentinel -> NULL.
-- Two ranking universes (CDC documentation): the U.S. file ranks counties nationally; the
-- stand-alone Puerto Rico file ranks municipios only against each other ("Puerto Rico is not
-- included in the U.S.-wide ranking"). RPL_* values are kept with their universe and must
-- never be compared across universes.
drop table if exists staging.svi_county cascade;
create table staging.svi_county as
with s as (
  select
    fips                                              as county_fips,
    st_abbr                                           as state_abbr,
    county                                            as county_name,
    nullif(staging.try_numeric(area_sqmi), -999)      as area_sqmi,
    nullif(staging.try_int(e_totpop), -999)           as svi_total_pop,
    nullif(staging.try_numeric(ep_pov150), -999)      as pct_below_150_poverty,
    nullif(staging.try_numeric(ep_unemp), -999)       as pct_unemployed,
    nullif(staging.try_numeric(ep_nohsdp), -999)      as pct_no_hs_diploma,
    nullif(staging.try_numeric(ep_uninsur), -999)     as pct_uninsured,
    nullif(staging.try_numeric(ep_age65), -999)       as pct_age65_plus,
    nullif(staging.try_numeric(ep_disabl), -999)      as pct_disability,
    nullif(staging.try_numeric(ep_limeng), -999)      as pct_limited_english,
    nullif(staging.try_numeric(ep_mobile), -999)      as pct_mobile_homes,
    nullif(staging.try_numeric(ep_noveh), -999)       as pct_no_vehicle,
    nullif(staging.try_numeric(rpl_theme1), -999)     as svi_socioeconomic,
    nullif(staging.try_numeric(rpl_theme2), -999)     as svi_household,
    nullif(staging.try_numeric(rpl_theme3), -999)     as svi_minority_language,
    nullif(staging.try_numeric(rpl_theme4), -999)     as svi_housing_transport,
    nullif(staging.try_numeric(rpl_themes), -999)     as svi_overall,
    case when _partition_key like '%puerto-rico%' then 'puerto_rico_only'
         else 'united_states_national' end            as svi_universe,
    _loaded_at
  from raw.cdc_svi_county
  where fips ~ '^[0-9]{5}$'
)
select distinct on (county_fips) * from s order by county_fips, _loaded_at desc;

alter table staging.svi_county add primary key (county_fips);
