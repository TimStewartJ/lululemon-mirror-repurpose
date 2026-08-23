(function(){
  var timeZone;
  var utcOffsetMinutes=0;
  var clock24Hour=false;
  function tick(){
    var now=new Date();
    var displayDate=new Date(now.getTime()+utcOffsetMinutes*60000);
    var timeOptions={hour:'2-digit',minute:'2-digit',hour12:!clock24Hour,timeZone:'UTC'};
    var dateOptions={weekday:'long',month:'long',day:'numeric'};
    dateOptions.timeZone='UTC';
    try{
      document.getElementById('time').textContent=displayDate.toLocaleTimeString([],timeOptions);
      document.getElementById('date').textContent=displayDate.toLocaleDateString([],dateOptions);
    }catch(error){
      delete timeOptions.timeZone;delete dateOptions.timeZone;
      document.getElementById('time').textContent=displayDate.toLocaleTimeString([],timeOptions);
      document.getElementById('date').textContent=displayDate.toLocaleDateString([],dateOptions);
    }
  }
  function status(){
    fetch('/api/v1/status').then(function(response){return response.json()}).then(function(snapshot){
      document.getElementById('name').textContent=snapshot.displayName||'Mirror';
      timeZone=snapshot.timeZone;
      utcOffsetMinutes=Number(snapshot.utcOffsetMinutes||0);
      clock24Hour=Boolean(snapshot.clock24Hour);
      document.getElementById('status').textContent=snapshot.wifi&&snapshot.wifi.connected
        ? snapshot.wifi.ssid+' · FCast ready'
        : 'Offline · dashboard controls available over USB';
    }).catch(function(){document.getElementById('status').textContent='Offline'});
  }
  tick();
  status();
  setInterval(tick,1000);
  setInterval(status,15000);
}());
