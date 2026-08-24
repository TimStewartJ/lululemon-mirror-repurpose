(function(){
  'use strict';

  var layout;
  var runtime;
  var lastLayoutText='';
  var widgetsRoot=document.getElementById('widgets');
  var dashboard=document.getElementById('dashboard');
  var photo=document.getElementById('background-photo');
  var shade=document.getElementById('background-shade');

  function fetchJson(path){
    return fetch(path,{cache:'no-store'}).then(function(response){
      if(!response.ok)throw new Error('Request failed');
      return response.json();
    });
  }

  function escapeHtml(value){
    return String(value==null?'':value)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
      .replace(/'/g,'&#39;');
  }

  function formatTime(){
    var offset=runtime?Number(runtime.utcOffsetMinutes||0):0;
    var value=new Date(Date.now()+offset*60000);
    var hours=value.getUTCHours();
    var minutes=String(value.getUTCMinutes());
    if(minutes.length<2)minutes='0'+minutes;
    if(runtime&&runtime.clock24Hour){
      return (hours<10?'0':'')+hours+':'+minutes;
    }
    var suffix=hours>=12?'PM':'AM';
    var displayHours=hours%12||12;
    return displayHours+':'+minutes+' '+suffix;
  }

  function formatDate(){
    var offset=runtime?Number(runtime.utcOffsetMinutes||0):0;
    var value=new Date(Date.now()+offset*60000);
    var weekdays=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    var months=['January','February','March','April','May','June','July','August','September','October','November','December'];
    return weekdays[value.getUTCDay()]+', '+months[value.getUTCMonth()]+' '+value.getUTCDate();
  }

  function formatUptime(seconds){
    var total=Math.max(0,Number(seconds||0));
    var days=Math.floor(total/86400);
    var hours=Math.floor((total%86400)/3600);
    var minutes=Math.floor((total%3600)/60);
    if(days)return days+'d '+hours+'h';
    if(hours)return hours+'h '+minutes+'m';
    return minutes+'m';
  }

  function scheduleValue(){
    if(!runtime||!runtime.automation)return 'Schedule off';
    if(runtime.automation.sleeping){
      return runtime.automation.sleepReason==='inactivity'?'Waiting for motion':'Sleeping';
    }
    if(!runtime.automation.enabled)return 'Schedule off';
    return 'Sleep '+runtime.automation.sleepTime;
  }

  function widgetContent(widget){
    var media=runtime&&runtime.media?runtime.media:{};
    var wifi=runtime&&runtime.wifi?runtime.wifi:{};
    switch(widget.type){
      case 'clock': return formatTime();
      case 'date': return formatDate();
      case 'name': return escapeHtml(runtime&&runtime.displayName?runtime.displayName:'Mirror');
      case 'wifi': return '<span class="widget-label">Wi-Fi</span><span class="widget-value">'+
        escapeHtml(wifi.connected?(wifi.ssid||'Online'):'Offline')+'</span>';
      case 'media': return '<span class="widget-label">Media</span><span class="widget-value">'+
        escapeHtml(media.title||media.state||'Idle')+'</span>';
      case 'schedule': return '<span class="widget-label">Rhythm</span><span class="widget-value">'+
        scheduleValue()+'</span>';
      case 'brightness': return '<span class="widget-label">Light</span><span class="widget-value">'+
        (runtime&&runtime.brightness!=null?runtime.brightness:'—')+'</span>';
      case 'fcast': return '<span class="widget-label">FCast</span><span class="widget-value">Ready</span>';
      case 'ble': return '<span class="widget-label">Bluetooth</span><span class="widget-value">'+
        escapeHtml(runtime&&runtime.bleProvisioning?runtime.bleProvisioning:'Unavailable')+'</span>';
      case 'uptime': return '<span class="widget-label">Uptime</span><span class="widget-value">'+
        formatUptime(runtime&&runtime.deviceUptimeSeconds)+'</span>';
      case 'motion':
        var automation=runtime&&runtime.automation?runtime.automation:{};
        var motion=automation.motion||{};
        var presence=!automation.motionEnabled?'Off':(motion.monitoring
          ?(typeof motion.lastMotionAgeSeconds==='number'&&motion.lastMotionAgeSeconds<30
            ?'Movement':'Watching')
          :'Unavailable');
        return '<span class="widget-label">Presence</span><span class="widget-value">'+
          presence+'</span>';
      case 'pairing': return '<span class="widget-label">Pair</span><span class="widget-value">'+
        escapeHtml(runtime&&runtime.pairingCode?runtime.pairingCode:'—')+'</span>';
      case 'note': return escapeHtml(widget.text||'');
      default: return '';
    }
  }

  function fontSize(widget){
    var height=dashboard.clientHeight*widget.h/1000;
    var width=dashboard.clientWidth*widget.w/1000;
    if(widget.type==='clock')return Math.max(32,Math.min(height*.72,width*.28));
    if(widget.type==='date')return Math.max(13,Math.min(height*.48,width*.09));
    if(widget.type==='name')return Math.max(13,Math.min(height*.46,width*.1));
    if(widget.type==='note')return Math.max(14,Math.min(height*.35,width*.075));
    return Math.max(11,Math.min(height*.42,width*.09));
  }

  function applyBackground(){
    if(!layout)return;
    var background=layout.background;
    photo.style.backgroundImage='';
    if(background.mode==='solid'){
      dashboard.style.background=background.primary;
    }else{
      dashboard.style.background='linear-gradient(155deg,'+
        background.primary+' 0%,'+background.secondary+' 100%)';
    }
    if(background.mode==='photo'&&background.photo){
      photo.style.backgroundImage='url("/photos/'+encodeURIComponent(background.photo)+'")';
    }
    shade.style.background='rgba(0,0,0,'+(Number(background.dim||0)/100)+')';
  }

  function render(){
    if(!layout||!runtime)return;
    applyBackground();
    widgetsRoot.innerHTML='';
    layout.widgets.forEach(function(widget){
      if(!widget.visible)return;
      var element=document.createElement('div');
      element.className='mirror-widget widget-'+widget.type+' align-'+widget.align+
        (['clock','date','name','note'].indexOf(widget.type)<0?' widget-metric':'');
      element.style.left=(widget.x/10)+'%';
      element.style.top=(widget.y/10)+'%';
      element.style.width=(widget.w/10)+'%';
      element.style.height=(widget.h/10)+'%';
      element.style.opacity=String(widget.opacity/100);
      element.style.color=widget.type==='clock'||widget.type==='date'||widget.type==='name'
        ?layout.textColor:layout.accentColor;
      element.style.fontSize=fontSize(widget)+'px';
      element.innerHTML=widgetContent(widget);
      widgetsRoot.appendChild(element);
    });
  }

  function refreshLayout(){
    return fetchJson('/api/v1/dashboard/layout').then(function(next){
      var text=JSON.stringify(next);
      if(text!==lastLayoutText){
        layout=next;
        lastLayoutText=text;
        render();
      }
    });
  }

  function refreshRuntime(){
    return fetchJson('/api/v1/dashboard/runtime').then(function(next){
      runtime=next;
      render();
    });
  }

  function refresh(){
    Promise.all([refreshLayout(),refreshRuntime()]).catch(function(){});
  }

  refresh();
  setInterval(refreshRuntime,5000);
  setInterval(refreshLayout,7000);
  window.addEventListener('resize',render);
}());
