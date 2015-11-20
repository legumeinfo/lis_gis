app.service('geoJsonService', function($http, $rootScope) {

  var COLORS_URL = '/static/grin_app/js/colors.json';
  
  var MAX_RECS = 200;
  var s = {}; // service/singleton we will return 
  s.updating = false;
  s.data = []; // an array of geoJson features
  s.map = null; // the leaflet map, Note: this belongs to
		// mapController! don't update it in this service.
  s.bounds = L.latLngBounds(L.latLng(0,0), L.latLng(0,0));
  s.colors = {};
  
  /* default values for search filters */
  s.limitToMapExtent = true;
  s.maxRecs = MAX_RECS;
  s.country = null;
  s.taxonQuery = null;
    
  // array of event names we are publishing
  s.events = ['updated', 'willUpdate'];
  
  s.setBounds = function(bounds, search) {
    if(s.bounds.equals(bounds) && s.data.length > 0) {
      // early out if the bounds is already set to same, and we have results
      return;
    }
    s.bounds = bounds;
    if(search) {
      s.search();
    }
  };
  
  s.setCountry = function(cty) {
    s.country = cty;
    s.search();
  };
  
  s.setMaxRecs = function(max) {
    s.maxRecs = max;
    s.search();
  };
  
  s.setLimitToMapExtent = function(bool) {
    s.limitToMapExtent = bool;   
    s.search();
  };

  s.setTaxonQuery = function(q) {
    s.taxonQuery = q;
    s.search();
  };

  s.getBoundsOfGeoJSONPoints = function() {
    /* Calculate a center and radius which all the geojson points fall
     * within */
    var boundsArr = [];
    _.each(s.data, function(d) {
      if(d.geometry.coordinates)  {
	// convert from geojson simple coords to leafletjs simple coords
	boundsArr.push([d.geometry.coordinates[1],
			d.geometry.coordinates[0]]);
      }
    });
    var bounds = new L.LatLngBounds(boundsArr);
    return bounds;
  };
  
  s.search = function() {
    s.updating = true;
    $http({
      url : 'search',
      method : 'GET',
      params : {
	q : s.taxonQuery,
  	ne_lat : s.bounds._northEast.lat,
  	ne_lng : s.bounds._northEast.lng,
  	sw_lat : s.bounds._southWest.lat,
  	sw_lng : s.bounds._southWest.lng,
	limit_geo_bounds : s.limitToMapExtent,
	country : s.country,
	limit: s.maxRecs,
      }
    }).then(
      function(resp) {
  	// success handler;
  	s.data = resp.data;
	s.updating = false;
	s.notify('updated');
      },
      function(resp) {
  	// error handler
  	console.log('Error:');
  	console.log(resp);
      });
  };
  
  s.colorCache = null;
  
  s.colorFeature = function(feature) {
    /* try to match the genus and species against the LIS colors json */
    var key = feature.properties.taxon;
    var val = _.get(s.colorCache, key, false);
    if(val) { return val; }
    var result = _.filter(s.colorCache, function(v,k) {
      return key.indexOf(k) !== -1;
    });
    if(result.length) {
      return result[0];
    }
    return 'grey';
  };
 
  s.init = function() {
    $http.get(COLORS_URL).then(function(resp) {
      // success function
      s.colors = resp.data;
      s.colorCache = {};
      _.each(s.colors, function(v,k) {
	if(v.color) {
	  var key = v.genus + ' '+ v.species;
	  s.colorCache[key] = v.color;
	}
      });
      s.notify('updated');
    }, function(resp) {
      // error function
    });
  };
  
  /* pub/sub event model adapted from here :
     http://www.codelord.net/2015/05/04/angularjs-notifying-about-changes-from-services-to-controllers/
  */
  s.subscribe = function(scope, eventName, callback) {
    if(! _.contains(s.events, eventName)) {
      throw 'invalid eventName ' + eventName;
    }
    var handler = $rootScope.$on('geoJsonService_'+eventName, callback);
    scope.$on('$destroy', handler);
    return handler;
  };
  
  s.notify = function(eventName) {
    $rootScope.$emit('geoJsonService_'+eventName);
  };

  s.init();
  return s;
});
