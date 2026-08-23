(function(){
  var timeZone;
  var utcOffsetMinutes=0;
  var clock24Hour=false;
  function tick(){
    var now=new Date();
    var displayDate=new Date(now.getTime()+utcOffsetMinutes*60000);
    var options={hour:'2-digit',minute:'2-digit',hour12:!clock24Hour,timeZone:'UTC'};
    try{document.getElementById('time').textContent=displayDate.toLocaleTimeString([],options)}
    catch(error){delete options.timeZone;document.getElementById('time').textContent=displayDate.toLocaleTimeString([],options)}
  }
  fetch('/api/v1/status').then(function(response){return response.json()}).then(function(status){
    timeZone=status.timeZone;
    utcOffsetMinutes=Number(status.utcOffsetMinutes||0);
    clock24Hour=Boolean(status.clock24Hour);
    document.getElementById('name').textContent=(status.displayName||'Mirror')+' will retry automatically.';
    tick();
  }).catch(tick);
  tick();
  setInterval(tick,1000);
}());
