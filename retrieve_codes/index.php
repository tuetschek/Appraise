<?php
error_reporting(E_ALL);
ini_set('display_errors', 1);

// Allow from any origin
if (isset($_SERVER['HTTP_ORIGIN'])) {
    header("Access-Control-Allow-Origin: {$_SERVER['HTTP_ORIGIN']}");
    header('Access-Control-Allow-Credentials: true');
    header('Access-Control-Max-Age: 86400');    // cache for 1 day
}
// Access-Control headers are received during OPTIONS requests
if ($_SERVER['REQUEST_METHOD'] == 'OPTIONS') {

    if (isset($_SERVER['HTTP_ACCESS_CONTROL_REQUEST_METHOD']))
        header("Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS");         

    if (isset($_SERVER['HTTP_ACCESS_CONTROL_REQUEST_HEADERS']))
        header("Access-Control-Allow-Headers: {$_SERVER['HTTP_ACCESS_CONTROL_REQUEST_HEADERS']}");

}

$URL = (!empty($_SERVER['HTTPS'])) ? "https://".$_SERVER['SERVER_NAME'].$_SERVER['REQUEST_URI'] : "http://".$_SERVER['SERVER_NAME'].$_SERVER['REQUEST_URI'];

if (isset($_POST['getcode'])){
    if ($db = new SQLite3('tokens.db')){
        $db->query('begin exclusive transaction');
        $res = $db->query('select * from tokens limit 1');
        while ($arr = $res->fetchArray()){
            echo("Váš přístupový kód: " . $arr['token']);
            $db->query('delete from tokens where token = "' . $arr['token'] . '"');
        }
        $db->query('commit transaction');
    }
}
else {
?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">

<head>
  <title>Přístupový kód k Appraise</title>
</head>
<script type="text/javascript">

function getXMLHttpRequest() {

    if (window.XMLHttpRequest) {
        return new window.XMLHttpRequest;
    }
    else {
        try {
            return new ActiveXObject("MSXML2.XMLHTTP.3.0");
        }
        catch(ex) {
            return null;
        }
    }
}

function getCode(){
    request = getXMLHttpRequest();
    if (request){
        var url = '<?php echo $URL ?>';
        request.open('POST', url, false);
        request.setRequestHeader("Content-type", "application/x-www-form-urlencoded");
        request.send("getcode=1");
        if (request.status == 200){
            document.getElementById('code').innerHTML = request.responseText;
            return;
        }
    }
    document.getElementById('code').innerHTML = 'Bohužel se nepodařilo získat kód. Zeptejte se Ondřeje, proč to nefunguje.'; 
}

</script>
<body onload="getCode();">
<p id="code">Získávám kód...</p>
</body>

</html>
<?php
}
?>
