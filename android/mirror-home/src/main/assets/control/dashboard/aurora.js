(function(){
  function tick(){
    var now=new Date();
    document.getElementById('time').textContent=now.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    document.getElementById('date').textContent=now.toLocaleDateString([], {weekday:'long',month:'long',day:'numeric'});
  }
  function status(){
    fetch('/api/v1/status').then(function(response){return response.json()}).then(function(snapshot){
      document.getElementById('name').textContent=snapshot.displayName||'Mirror';
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
