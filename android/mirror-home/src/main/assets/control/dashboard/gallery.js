(function(){
  var photos=[];
  var index=-1;
  var utcOffsetMinutes=0;
  var clock24Hour=false;
  function tick(){
    var displayDate=new Date(Date.now()+utcOffsetMinutes*60000);
    var options={hour:'2-digit',minute:'2-digit',hour12:!clock24Hour,timeZone:'UTC'};
    document.getElementById('clock').textContent=displayDate.toLocaleTimeString([],options);
  }
  function next(){
    if(!photos.length)return;
    var images=document.querySelectorAll('.photo');
    for(var i=0;i<images.length;i++)images[i].classList.remove('visible');
    index=(index+1)%photos.length;
    images[index].classList.add('visible');
  }
  function load(){
    Promise.all([
      fetch('/api/v1/photos/slideshow').then(function(response){return response.json()}),
      fetch('/api/v1/status').then(function(response){return response.json()})
    ]).then(function(results){
      photos=results[0].photos||[];
      var status=results[1];
      utcOffsetMinutes=Number(status.utcOffsetMinutes||0);
      clock24Hour=Boolean(status.clock24Hour);
      document.getElementById('name').textContent=status.displayName||'Mirror';
      var stage=document.getElementById('stage');
      var previous=stage.querySelectorAll('.photo');
      for(var i=0;i<previous.length;i++)previous[i].remove();
      index=-1;
      document.getElementById('empty').classList.toggle('hidden',photos.length>0);
      photos.forEach(function(photo){
        var image=document.createElement('img');
        image.className='photo';
        image.alt='';
        image.src='/photos/'+encodeURIComponent(photo.name);
        stage.appendChild(image);
      });
      next();
    }).catch(function(){});
  }
  tick();
  load();
  setInterval(tick,1000);
  setInterval(next,15000);
  setInterval(load,30000);
}());
